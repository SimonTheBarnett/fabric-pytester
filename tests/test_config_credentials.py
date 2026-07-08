import json

import pytest

from fabric_pytester.core.config import TimeoutConfig, load_compat_api_fabric, load_config
from fabric_pytester.core.credentials import (
    AliasedSecretProvider,
    EnvSecretProvider,
    LocalSecretProvider,
    MappingSecretProvider,
)
from fabric_pytester.core.errors import SecretError
from fabric_pytester.core.logging import redact_mapping


def test_load_config_from_toml(tmp_path):
    config_path = tmp_path / "fabric-pytester.toml"
    config_path.write_text(
        """
        [tool.fabric-pytester]
        scenario_paths = ["scenarios"]
        artifact_dir = "artifacts"
        debug = true
        sql_diagnostics = true

        [tool.fabric-pytester.environments.dev.fabric]
        workspace_id = "workspace"
        job_api_style = "query"
        sql_hostname = "host"
        sql_database = "db"

        [tool.fabric-pytester.environments.dev.sql_backends.source_jdbc]
        sql_adapter = "jdbc"
        jdbc_url = "jdbc:vendor://example"
        driver_class = "com.vendor.jdbc.Driver"
        """,
        encoding="utf-8",
    )
    config = load_config(env_name="dev", config_path=config_path, root=tmp_path)
    assert config.scenario_paths == [(tmp_path / "scenarios").resolve()]
    assert config.artifact_dir == (tmp_path / "artifacts").resolve()
    assert config.environment.fabric["workspace_id"] == "workspace"
    assert config.environment.fabric["job_api_style"] == "query"
    assert config.environment.sql_backends["source_jdbc"]["sql_adapter"] == "jdbc"
    assert config.debug is True
    assert config.sql_diagnostics is True


def test_load_standalone_top_level_fabric_pytester_toml(tmp_path):
    config_path = tmp_path / "fabric-pytester.toml"
    config_path.write_text(
        """
        scenario_paths = ["tests/e2e/fabric/scenarios"]
        artifact_dir = "results/artifacts/fabric"
        pytest_env_option = "env"

        [timeouts]
        fabric_job_seconds = 3600
        fabric_sql_seconds = 300
        fabric_poll_interval_seconds = 10

        [environments.qa.fabric]
        workspace_id = "workspace"
        sql_hostname = "host"
        sql_database = "database"

        [environments.qa.sql_backends.reporting_db]
        sql_adapter = "pyodbc"
        connection_string = "Driver={ODBC Driver 18 for SQL Server};Server=tcp:host,1433;"
        """,
        encoding="utf-8",
    )

    config = load_config(env_name="qa", config_path=config_path, root=tmp_path)

    assert config.pytest_env_option == "env"
    assert config.scenario_paths == [(tmp_path / "tests/e2e/fabric/scenarios").resolve()]
    assert config.environment.fabric["workspace_id"] == "workspace"
    assert config.environment.sql_backends["reporting_db"]["sql_adapter"] == "pyodbc"


def test_default_fabric_job_timeouts_are_shorter_for_normal_runs():
    timeouts = TimeoutConfig()

    assert timeouts.fabric_job_seconds == 3600
    assert timeouts.fabric_poll_interval_seconds == 10


def test_load_compat_api_fabric(tmp_path):
    path = tmp_path / "api_fabric.json"
    path.write_text(json.dumps({"dev": {"workspace_id": "abc"}}), encoding="utf-8")
    assert load_compat_api_fabric(path, "dev") == {"workspace_id": "abc"}


def test_local_secret_provider_reads_env_block(tmp_path):
    path = tmp_path / "debug-env.json"
    path.write_text(json.dumps({"dev": {"FABRIC_CLIENT_ID": "client"}}), encoding="utf-8")
    provider = LocalSecretProvider("dev", path)
    assert provider.require("FABRIC_CLIENT_ID") == "client"


def test_env_secret_provider(monkeypatch):
    monkeypatch.setenv("FABRIC_CLIENT_ID", "client")
    assert EnvSecretProvider().get("FABRIC_CLIENT_ID") == "client"


def test_secret_require_raises():
    with pytest.raises(SecretError):
        MappingSecretProvider({}).require("MISSING")


def test_aliased_secret_provider_resolves_project_secret_names():
    provider = AliasedSecretProvider(
        MappingSecretProvider({"fabric_client_id": "client"}),
        aliases={"FABRIC_CLIENT_ID": "fabric_client_id"},
    )
    assert provider.require("FABRIC_CLIENT_ID") == "client"


def test_redact_mapping_redacts_nested_secret_values():
    redacted = redact_mapping({"client_secret": "abcdef", "nested": {"password": "pass"}})
    assert redacted["client_secret"] == "ab***ef"
    assert redacted["nested"]["password"] == "***"
