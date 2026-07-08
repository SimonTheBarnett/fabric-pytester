from __future__ import annotations

import json
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from fabric_pytester.core.errors import ConfigError


@dataclass(slots=True)
class TimeoutConfig:
    fabric_job_seconds: int = 3600
    fabric_sql_seconds: int = 300
    fabric_poll_interval_seconds: int = 10


@dataclass(slots=True)
class FabricEnvironmentConfig:
    name: str
    fabric: dict[str, Any] = field(default_factory=dict)
    onelake: dict[str, Any] = field(default_factory=dict)
    sql_backends: dict[str, dict[str, Any]] = field(default_factory=dict)
    secrets: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class FabricPytesterConfig:
    env_name: str
    root: Path
    scenario_paths: list[Path]
    artifact_dir: Path
    pytest_env_option: str | None = None
    debug: bool = False
    sql_diagnostics: bool = False
    timeouts: TimeoutConfig = field(default_factory=TimeoutConfig)
    environment: FabricEnvironmentConfig = field(
        default_factory=lambda: FabricEnvironmentConfig(name="dev")
    )
    raw: dict[str, Any] = field(default_factory=dict)


def load_toml_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"Fabric config file does not exist: {path}")
    with path.open("rb") as handle:
        raw = tomllib.load(handle)
    if path.name == "pyproject.toml":
        return raw.get("tool", {}).get("fabric-pytester", {})
    return raw.get("tool", {}).get("fabric-pytester", raw.get("fabric-pytester", raw))


def _resolve_paths(root: Path, values: list[str] | tuple[str, ...] | None) -> list[Path]:
    if not values:
        return []
    return [
        (root / value).resolve() if not Path(value).is_absolute() else Path(value)
        for value in values
    ]


def load_config(
    *,
    env_name: str,
    config_path: Path | None,
    scenario_paths: list[str] | None = None,
    artifact_dir: str | None = None,
    root: Path | None = None,
) -> FabricPytesterConfig:
    root = (root or Path.cwd()).resolve()
    raw: dict[str, Any] = {}
    if config_path is None:
        candidate = root / "fabric-pytester.toml"
        config_path = candidate if candidate.exists() else root / "pyproject.toml"
    if config_path.exists():
        raw = load_toml_config(config_path)
    elif config_path.name != "pyproject.toml":
        raise ConfigError(f"Fabric config file does not exist: {config_path}")

    timeouts = TimeoutConfig(**{**asdict(TimeoutConfig()), **raw.get("timeouts", {})})
    envs = raw.get("environments", {})
    env_raw = envs.get(env_name, {}) if isinstance(envs, dict) else {}
    env_config = FabricEnvironmentConfig(
        name=env_name,
        fabric=dict(env_raw.get("fabric", {})),
        onelake=dict(env_raw.get("onelake", {})),
        sql_backends=dict(env_raw.get("sql_backends", {})),
        secrets=dict(env_raw.get("secrets", {})),
    )
    resolved_scenario_paths = _resolve_paths(
        root,
        scenario_paths or raw.get("scenario_paths") or [],
    )
    resolved_artifact_dir = root / (
        artifact_dir or raw.get("artifact_dir", "results/artifacts/fabric")
    )
    pytest_env_option = raw.get("pytest_env_option")
    return FabricPytesterConfig(
        env_name=env_name,
        root=root,
        scenario_paths=resolved_scenario_paths,
        artifact_dir=resolved_artifact_dir.resolve(),
        pytest_env_option=str(pytest_env_option) if pytest_env_option else None,
        debug=bool(raw.get("debug", False)),
        sql_diagnostics=bool(raw.get("sql_diagnostics", False)),
        timeouts=timeouts,
        environment=env_config,
        raw=raw,
    )


def load_compat_api_fabric(path: Path, env_name: str) -> dict[str, Any]:
    """Load the legacy data/<env>/api_fabric.json shape as plain config."""
    if not path.exists():
        raise ConfigError(f"Compatibility config file does not exist: {path}")
    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ConfigError(f"Compatibility config must be a JSON object: {path}")
    return raw.get(env_name, raw)
