from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

import pytest

from fabric_pytester.core.config import FabricPytesterConfig, load_config, load_toml_config
from fabric_pytester.core.credentials import (
    AliasedSecretProvider,
    ChainedSecretProvider,
    EnvSecretProvider,
    KeyVaultSecretProvider,
    LocalSecretProvider,
    MappingSecretProvider,
    SecretProvider,
    env_or_skip,
)
from fabric_pytester.core.destinations import SqlBackendDestination
from fabric_pytester.core.errors import ConfigError
from fabric_pytester.core.fabric_jobs import FabricClient
from fabric_pytester.core.http import BearerHttpClient
from fabric_pytester.core.onelake_client import OneLakeClient
from fabric_pytester.core.renderer import PlaceholderProvider, RandomHelper
from fabric_pytester.core.runner import ScenarioGroupContext, ScenarioGroupState, ScenarioRunner
from fabric_pytester.core.scenario_loader import ScenarioLoader
from fabric_pytester.core.sql_backend import LazySqlBackend, default_registry
from fabric_pytester.core.tokens import (
    FABRIC_SCOPE,
    ClientCredentialsTokenProvider,
    ClientCredentialsUrlTokenProvider,
    TokenProvider,
    coerce_token_provider,
)

DEFAULT_SECRET_ALIASES = {
    "FABRIC_CLIENT_ID": "fabric_client_id",
    "FABRIC_CLIENT_SECRET": "fabric_client_secret",
    "FABRIC_TENANT_ID": "fabric_tenant_id",
}
LOGGER = logging.getLogger(__name__)


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("fabric-pytester")
    group.addoption("--fabric-env", action="store", default=None, help="Fabric environment name.")
    group.addoption(
        "--fabric-config", action="store", default=None, help="Path to fabric-pytester TOML config."
    )
    group.addoption(
        "--fabric-scenarios", action="append", default=None, help="Scenario JSON file or directory."
    )
    group.addoption(
        "--fabric-keep-artifacts", action="store_true", help="Keep tracked artifacts and inputs."
    )
    group.addoption(
        "--fabric-timeout-job",
        action="store",
        type=int,
        default=None,
        help="Fabric job timeout in seconds.",
    )
    group.addoption(
        "--fabric-timeout-sql",
        action="store",
        type=int,
        default=None,
        help="SQL polling timeout in seconds.",
    )
    group.addoption(
        "--fabric-poll-interval",
        action="store",
        type=int,
        default=None,
        help="Fabric polling interval.",
    )
    group.addoption(
        "--fabric-auth-provider",
        action="store",
        default=None,
        choices=("local", "env", "keyvault"),
        help="Secret/auth provider.",
    )
    group.addoption(
        "--fabric-job-api-style",
        action="store",
        default=None,
        choices=("path", "query"),
        help="Fabric job API style.",
    )
    group.addoption(
        "--fabric-debug",
        action="store_true",
        help="Print fabric-pytester debug diagnostics.",
    )
    group.addoption(
        "--fabric-sql-diagnostics",
        action="store_true",
        help="Run SQL connection diagnostics after SQL backends are created.",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "fabric_scenarios(names=None): parameterize a test once per Fabric scenario key.",
    )


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    marker = metafunc.definition.get_closest_marker("fabric_scenarios")
    if marker is None:
        return
    if "fabric_current_scenario" not in metafunc.fixturenames:
        raise pytest.UsageError(
            "@pytest.mark.fabric_scenarios requires the fabric_runner fixture or "
            "fabric_current_scenario fixture"
        )
    names = _marker_scenario_names(marker, metafunc.config)
    metafunc.parametrize("fabric_current_scenario", names, indirect=True, ids=names)


@pytest.fixture
def make_random() -> RandomHelper:
    return RandomHelper()


@pytest.fixture
def fabric_env_name(request: pytest.FixtureRequest) -> str:
    return (
        request.config.getoption("--fabric-env")
        or _mapped_pytest_env_name(request)
        or os.environ.get("FABRIC_ENV")
        or "dev"
    )


@pytest.fixture
def fabric_config(request: pytest.FixtureRequest, fabric_env_name: str) -> FabricPytesterConfig:
    config_opt = request.config.getoption("--fabric-config")
    scenario_opts = request.config.getoption("--fabric-scenarios")
    config = load_config(
        env_name=fabric_env_name,
        config_path=Path(config_opt).resolve() if config_opt else None,
        scenario_paths=scenario_opts,
        root=Path(str(request.config.rootpath)),
    )
    timeout_job = request.config.getoption("--fabric-timeout-job")
    timeout_sql = request.config.getoption("--fabric-timeout-sql")
    poll_interval = request.config.getoption("--fabric-poll-interval")
    if timeout_job:
        config.timeouts.fabric_job_seconds = timeout_job
    if timeout_sql:
        config.timeouts.fabric_sql_seconds = timeout_sql
    if poll_interval:
        config.timeouts.fabric_poll_interval_seconds = poll_interval
    if request.config.getoption("--fabric-debug"):
        config.debug = True
    if request.config.getoption("--fabric-sql-diagnostics"):
        config.sql_diagnostics = True
    return config


@pytest.fixture
def fabric_debug_env(fabric_env_name: str) -> LocalSecretProvider:
    return LocalSecretProvider(fabric_env_name)


@pytest.fixture
def fabric_credentials(
    request: pytest.FixtureRequest,
    fabric_config: FabricPytesterConfig,
    fabric_debug_env: LocalSecretProvider,
) -> SecretProvider:
    provider_name = (
        request.config.getoption("--fabric-auth-provider")
        or fabric_config.environment.secrets.get("provider")
        or "local"
    )
    providers: list[SecretProvider] = []
    if provider_name == "local":
        providers.extend([fabric_debug_env, EnvSecretProvider()])
    elif provider_name == "env":
        providers.append(EnvSecretProvider())
    elif provider_name == "auth_token":
        providers.append(MappingSecretProvider(_auth_token_secrets(request)))
    elif provider_name == "keyvault":
        secrets = fabric_config.environment.secrets
        providers.extend(
            [
                KeyVaultSecretProvider(
                    vault_url=secrets["vault_url"],
                    names=secrets.get("names", {}),
                ),
                EnvSecretProvider(),
            ]
        )
    aliases = {
        **DEFAULT_SECRET_ALIASES,
        **fabric_config.environment.secrets.get("aliases", {}),
        **fabric_config.environment.secrets.get("names", {}),
    }
    return AliasedSecretProvider(ChainedSecretProvider(providers), aliases=aliases)


@pytest.fixture
def fabric_env_or_skip(fabric_credentials: SecretProvider) -> Callable[[str], str]:
    return lambda name: env_or_skip(fabric_credentials, name)


@pytest.fixture
def fabric_token_provider(
    request: pytest.FixtureRequest, fabric_credentials: SecretProvider
) -> TokenProvider:
    token_config = _token_provider_config(request.getfixturevalue("fabric_config"))
    provider_name = token_config.get("token_provider")
    if provider_name == "auth_token":
        return coerce_token_provider(request.getfixturevalue("auth_token"))
    if provider_name == "client_credentials_url":
        return ClientCredentialsUrlTokenProvider(
            secrets=fabric_credentials,
            auth_url=str(token_config["auth_url"]),
            client_id_secret=str(token_config.get("client_id_secret", "FABRIC_CLIENT_ID")),
            client_secret_secret=str(
                token_config.get("client_secret_secret", "FABRIC_CLIENT_SECRET")
            ),
            grant_type=str(token_config.get("grant_type", "client_credentials")),
        )
    return ClientCredentialsTokenProvider(fabric_credentials)


@pytest.fixture
def fabric_http(
    fabric_config: FabricPytesterConfig, fabric_token_provider: TokenProvider
) -> BearerHttpClient:
    base_url = fabric_config.environment.fabric.get("base_url", "https://api.fabric.microsoft.com")
    return BearerHttpClient(
        base_url=base_url, token_provider=fabric_token_provider, scope=FABRIC_SCOPE
    )


@pytest.fixture
def fabric_client(
    request: pytest.FixtureRequest,
    fabric_config: FabricPytesterConfig,
    fabric_http: BearerHttpClient,
) -> FabricClient:
    workspace_id = fabric_config.environment.fabric.get("workspace_id")
    if not workspace_id:
        pytest.skip("Fabric workspace_id is not configured")
    return FabricClient(
        http=fabric_http,
        workspace_id=workspace_id,
        job_api_style=_job_api_style(request, fabric_config),
        default_timeout_seconds=fabric_config.timeouts.fabric_job_seconds,
        default_poll_interval_seconds=fabric_config.timeouts.fabric_poll_interval_seconds,
    )


@pytest.fixture
def onelake_client(
    fabric_config: FabricPytesterConfig, fabric_token_provider: TokenProvider
) -> OneLakeClient:
    cfg = fabric_config.environment.onelake
    for key in ("account_url", "workspace", "lakehouse_root"):
        if not cfg.get(key):
            pytest.skip(f"OneLake {key} is not configured")
    return OneLakeClient(
        account_url=cfg["account_url"],
        workspace=cfg["workspace"],
        lakehouse_root=cfg["lakehouse_root"],
        token_provider=fabric_token_provider,
    )


@pytest.fixture
def fabric_sql(
    fabric_config: FabricPytesterConfig,
    fabric_credentials: SecretProvider,
    fabric_token_provider: TokenProvider,
) -> Any:
    cfg = fabric_config.environment.fabric
    if not cfg.get("sql_hostname") or not cfg.get("sql_database"):
        pytest.skip("Fabric SQL endpoint is not configured")
    secrets = _sql_secrets(cfg, fabric_credentials)
    prepared = _prepare_sql_config(cfg, fabric_config.root, fabric_config.debug)
    backend = default_registry().create(
        "fabric", prepared, secrets, token_provider=fabric_token_provider
    )
    _run_sql_diagnostics(backend, fabric_config.sql_diagnostics)
    return backend


@pytest.fixture
def fabric_sql_backends(
    fabric_config: FabricPytesterConfig,
    fabric_credentials: SecretProvider,
    fabric_token_provider: TokenProvider,
) -> dict[str, LazySqlBackend]:
    registry = default_registry()

    def make_backend(config: dict[str, Any]) -> Any:
        prepared = _prepare_sql_config(config, fabric_config.root, fabric_config.debug)
        backend = registry.create(
            str(config.get("name", config.get("sql_adapter", "jdbc"))),
            prepared,
            _sql_secrets(prepared, fabric_credentials),
            token_provider=fabric_token_provider,
        )
        _run_sql_diagnostics(backend, fabric_config.sql_diagnostics)
        return backend

    return {
        name: LazySqlBackend(lambda config=config: make_backend(config))
        for name, config in fabric_config.environment.sql_backends.items()
    }


@pytest.fixture
def fabric_placeholder_providers() -> list[PlaceholderProvider]:
    return []


@pytest.fixture
def fabric_scenario_loader(fabric_config: FabricPytesterConfig) -> ScenarioLoader:
    if not fabric_config.scenario_paths:
        pytest.skip("No Fabric scenario paths are configured")
    return ScenarioLoader(fabric_config.scenario_paths)


@pytest.fixture
def fabric_current_scenario(request: pytest.FixtureRequest) -> str | None:
    return getattr(request, "param", None)


@pytest.fixture(scope="session")
def fabric_scenario_group_state() -> Generator[ScenarioGroupState]:
    state = ScenarioGroupState()
    yield state
    state.cleanup()


@pytest.fixture
def fabric_runner(
    request: pytest.FixtureRequest,
    fabric_config: FabricPytesterConfig,
    fabric_scenario_loader: ScenarioLoader,
    fabric_credentials: SecretProvider,
    fabric_token_provider: TokenProvider,
    fabric_sql_backends: dict[str, LazySqlBackend],
    fabric_placeholder_providers: list[PlaceholderProvider],
    fabric_current_scenario: str | None,
    fabric_scenario_group_state: ScenarioGroupState,
) -> ScenarioRunner:
    fabric_client = None
    onelake = None
    fabric_sql = None
    fabric_cfg = fabric_config.environment.fabric
    if fabric_cfg.get("workspace_id"):
        fabric_http = BearerHttpClient(
            base_url=fabric_cfg.get("base_url", "https://api.fabric.microsoft.com"),
            token_provider=fabric_token_provider,
            scope=FABRIC_SCOPE,
        )
        fabric_client = FabricClient(
            http=fabric_http,
            workspace_id=fabric_cfg["workspace_id"],
            job_api_style=_job_api_style(request, fabric_config),
            default_timeout_seconds=fabric_config.timeouts.fabric_job_seconds,
            default_poll_interval_seconds=fabric_config.timeouts.fabric_poll_interval_seconds,
        )
    onelake_cfg = fabric_config.environment.onelake
    if all(onelake_cfg.get(key) for key in ("account_url", "workspace", "lakehouse_root")):
        onelake = OneLakeClient(
            account_url=onelake_cfg["account_url"],
            workspace=onelake_cfg["workspace"],
            lakehouse_root=onelake_cfg["lakehouse_root"],
            token_provider=fabric_token_provider,
        )
    if fabric_cfg.get("sql_hostname") and fabric_cfg.get("sql_database"):

        def make_sql() -> Any:
            prepared = _prepare_sql_config(fabric_cfg, fabric_config.root, fabric_config.debug)
            backend = default_registry().create(
                "fabric",
                prepared,
                _sql_secrets(prepared, fabric_credentials),
                token_provider=fabric_token_provider,
            )
            _run_sql_diagnostics(backend, fabric_config.sql_diagnostics)
            return backend

        fabric_sql = LazySqlBackend(make_sql)
    runner = ScenarioRunner(
        loader=fabric_scenario_loader,
        fabric_client=fabric_client,
        onelake_client=onelake,
        fabric_sql=fabric_sql,
        placeholder_providers=fabric_placeholder_providers,
        artifact_dir=fabric_config.artifact_dir,
        keep_artifacts=request.config.getoption("--fabric-keep-artifacts"),
        fabric_sql_seconds=fabric_config.timeouts.fabric_sql_seconds,
        fabric_poll_interval_seconds=fabric_config.timeouts.fabric_poll_interval_seconds,
        debug=fabric_config.debug,
        current_scenario_key=fabric_current_scenario,
        scenario_group_context=_scenario_group_context(
            request,
            fabric_current_scenario,
            fabric_scenario_group_state,
        ),
    )
    for name, backend in fabric_sql_backends.items():
        runner.add_destination(
            name,
            SqlBackendDestination(
                backend,
                default_timeout_seconds=fabric_config.timeouts.fabric_sql_seconds,
                default_poll_interval_seconds=fabric_config.timeouts.fabric_poll_interval_seconds,
                debug=fabric_config.debug,
            ),
        )
    return runner


@pytest.fixture
def make_fabric_file(onelake_client: OneLakeClient) -> Generator[Callable[..., Any]]:
    created = []

    def factory(**kwargs: Any) -> Any:
        uploaded = onelake_client.upload(**kwargs)
        created.append(uploaded)
        return uploaded

    yield factory

    for uploaded in reversed(created):
        onelake_client.delete(uploaded.path)


@pytest.fixture
def make_fabric_job_run(fabric_client: FabricClient) -> Callable[..., Any]:
    def factory(**kwargs: Any) -> Any:
        run = fabric_client.run_item_job(**kwargs)
        return run.wait()

    return factory


def _auth_token_secrets(request: pytest.FixtureRequest) -> dict[str, Any]:
    value = request.getfixturevalue("auth_token")
    if not isinstance(value, dict):
        raise TypeError("auth_token must return a dict when secrets.provider = 'auth_token'")
    return value


def _scenario_group_context(
    request: pytest.FixtureRequest,
    current_scenario: str | None,
    state: ScenarioGroupState,
) -> ScenarioGroupContext | None:
    if current_scenario is None:
        return None
    marker = request.node.get_closest_marker("fabric_scenarios")
    if marker is None:
        return None
    scenario_names = _marker_scenario_names(marker, request.config)
    group_key = _scenario_group_key(request.node.nodeid)
    state.run_for(group_key, len(scenario_names))
    request.addfinalizer(lambda: state.finish_item(group_key))
    return ScenarioGroupContext(
        state=state,
        group_key=group_key,
        expected_count=len(scenario_names),
    )


def _scenario_group_key(nodeid: str) -> str:
    return re.sub(r"\[[^\]]+\]$", "", nodeid)


def _marker_scenario_names(marker: pytest.Mark, config: pytest.Config) -> list[str]:
    names = _explicit_marker_scenario_names(marker)
    if names is None:
        names = _discover_scenario_names(config)
    if not names:
        raise pytest.UsageError("@pytest.mark.fabric_scenarios did not resolve any scenarios")
    return names


def _explicit_marker_scenario_names(marker: pytest.Mark) -> list[str] | None:
    if "names" in marker.kwargs:
        return _coerce_marker_names(marker.kwargs["names"])
    if "scenarios" in marker.kwargs:
        return _coerce_marker_names(marker.kwargs["scenarios"])
    if not marker.args:
        return None
    if len(marker.args) == 1 and not isinstance(marker.args[0], str):
        return _coerce_marker_names(marker.args[0])
    return [str(value) for value in marker.args]


def _coerce_marker_names(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _discover_scenario_names(config: pytest.Config) -> list[str]:
    try:
        loaded = load_config(
            env_name=_fabric_env_name_from_config(config),
            config_path=_fabric_config_path(config),
            scenario_paths=config.getoption("--fabric-scenarios"),
            root=Path(str(config.rootpath)),
        )
        return list(ScenarioLoader(loaded.scenario_paths).load())
    except Exception as exc:
        raise pytest.UsageError(f"Unable to discover Fabric scenarios: {exc}") from exc


def _fabric_env_name_from_config(config: pytest.Config) -> str:
    return (
        config.getoption("--fabric-env")
        or _mapped_pytest_env_name_from_config(config)
        or os.environ.get("FABRIC_ENV")
        or "dev"
    )


def _mapped_pytest_env_name_from_config(config: pytest.Config) -> str | None:
    config_path = _fabric_config_path(config)
    if config_path is None:
        root = Path(str(config.rootpath))
        candidate = root / "fabric-pytester.toml"
        config_path = candidate if candidate.exists() else root / "pyproject.toml"
    if not config_path.exists():
        return None
    option_name = load_toml_config(config_path).get("pytest_env_option")
    if not option_name:
        return None
    option = str(option_name)
    option_value = config.getoption(option, default=None)
    if option_value is None and not option.startswith("--"):
        option_value = config.getoption(f"--{option}", default=None)
    return str(option_value) if option_value else None


def _fabric_config_path(config: pytest.Config) -> Path | None:
    config_opt = config.getoption("--fabric-config")
    return Path(config_opt).resolve() if config_opt else None


def _token_provider_config(config: FabricPytesterConfig) -> dict[str, Any]:
    return {
        **config.environment.secrets,
        **config.environment.fabric,
    }


def _job_api_style(request: pytest.FixtureRequest, config: FabricPytesterConfig) -> str:
    return (
        request.config.getoption("--fabric-job-api-style")
        or config.environment.fabric.get("job_api_style")
        or "path"
    )


def _mapped_pytest_env_name(request: pytest.FixtureRequest) -> str | None:
    config_opt = request.config.getoption("--fabric-config")
    root = Path(str(request.config.rootpath))
    config_path = Path(config_opt).resolve() if config_opt else None
    if config_path is None:
        candidate = root / "fabric-pytester.toml"
        config_path = candidate if candidate.exists() else root / "pyproject.toml"
    if not config_path.exists():
        return None
    option_name = load_toml_config(config_path).get("pytest_env_option")
    if not option_name:
        return None
    option = str(option_name)
    option_value = request.config.getoption(option, default=None)
    if option_value is None and not option.startswith("--"):
        option_value = request.config.getoption(f"--{option}", default=None)
    return str(option_value) if option_value else None


def _sql_secrets(config: dict[str, Any], credentials: SecretProvider) -> dict[str, str]:
    adapter = str(config.get("adapter") or config.get("sql_adapter") or "fabric")
    if adapter == "fabric-jdbc-token":
        return {}
    if adapter == "jdbc":
        return _configured_secret_values(config, credentials)
    if adapter == "pyodbc" and config.get("connection_string"):
        return _configured_secret_values(config, credentials)
    if config.get("client_id") and config.get("client_secret"):
        return _configured_secret_values(config, credentials)
    return {
        "FABRIC_CLIENT_ID": credentials.require("FABRIC_CLIENT_ID"),
        "FABRIC_CLIENT_SECRET": credentials.require("FABRIC_CLIENT_SECRET"),
        **_configured_secret_values(config, credentials),
    }


def _prepare_sql_config(config: dict[str, Any], root: Path, debug: bool) -> dict[str, Any]:
    prepared = dict(config)
    adapter = _sql_adapter(prepared)
    LOGGER.info("Active SQL adapter: %s", adapter)
    _debug_print(
        debug,
        f"Active SQL adapter={adapter} host={prepared.get('sql_hostname')} "
        f"database={prepared.get('sql_database')}",
    )
    if adapter not in {"jdbc", "fabric-jdbc-token"}:
        return prepared
    driver_path = prepared.get("driver_path")
    if not driver_path:
        return prepared
    path = Path(str(driver_path))
    resolved = path if path.is_absolute() else (root / path).resolve()
    exists = resolved.exists()
    _debug_print(debug, f"JDBC driver_path={resolved} exists={exists}")
    if not exists:
        raise ConfigError(f"Configured JDBC driver file does not exist: {resolved}")
    prepared["driver_path"] = str(resolved)
    return prepared


def _sql_adapter(config: dict[str, Any]) -> str:
    return str(config.get("adapter") or config.get("sql_adapter") or "mssql-python")


def _run_sql_diagnostics(backend: Any, enabled: bool) -> None:
    if not enabled:
        return
    info = getattr(backend, "info", None)
    adapter = getattr(info, "adapter", "<unknown>")
    database = None
    error = None
    try:
        row = backend.fetch_one("SELECT DB_NAME()")
        if row:
            database = next(iter(row.values()), None)
    except Exception as exc:
        error = exc
    if error is None:
        _debug_print(True, f"SQL diagnostics adapter={adapter} database={database}")
    else:
        _debug_print(True, f"SQL diagnostics adapter={adapter} failed={error}")


def _debug_print(enabled: bool, message: str) -> None:
    if enabled:
        print(f"[fabric-pytester] {message}")


def _configured_secret_values(
    config: dict[str, Any], credentials: SecretProvider
) -> dict[str, str]:
    values: dict[str, str] = {}
    for key in (
        "user_secret",
        "password_secret",
        "client_id_secret",
        "client_secret_secret",
    ):
        secret_name = config.get(key)
        if secret_name:
            values[str(secret_name)] = credentials.require(str(secret_name))
    return values
