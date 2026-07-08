from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest

from fabric_pytester.core.errors import SecretError

DEFAULT_DEBUG_ENV_PATH = Path.home() / ".fabric-pytester" / "debug-env.json"


class SecretProvider(ABC):
    @abstractmethod
    def get(self, name: str, default: str | None = None) -> str | None:
        """Return a secret value or default."""

    def require(self, name: str) -> str:
        value = self.get(name)
        if value is None or value == "":
            raise SecretError(f"Missing required secret or environment value: {name}")
        return value


@dataclass
class MappingSecretProvider(SecretProvider):
    values: Mapping[str, Any] = field(default_factory=dict)

    def get(self, name: str, default: str | None = None) -> str | None:
        value = self.values.get(name, default)
        if value is None:
            return None
        return str(value)


@dataclass
class EnvSecretProvider(SecretProvider):
    prefix: str = ""

    def get(self, name: str, default: str | None = None) -> str | None:
        return os.environ.get(f"{self.prefix}{name}", os.environ.get(name, default))


@dataclass
class LocalSecretProvider(SecretProvider):
    env_name: str
    path: Path = DEFAULT_DEBUG_ENV_PATH

    def __post_init__(self) -> None:
        self._values = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        with self.path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        env_values = raw.get(self.env_name, raw)
        if not isinstance(env_values, dict):
            raise SecretError(f"Local debug env for {self.env_name!r} must be an object")
        return env_values

    def get(self, name: str, default: str | None = None) -> str | None:
        value = self._values.get(name, default)
        if value is None:
            return None
        return str(value)


@dataclass
class KeyVaultSecretProvider(SecretProvider):
    vault_url: str
    names: Mapping[str, str] = field(default_factory=dict)
    credential: Any | None = None

    def __post_init__(self) -> None:
        if self.credential is None:
            try:
                from azure.identity import DefaultAzureCredential
            except ImportError as exc:  # pragma: no cover - import guard
                raise SecretError(
                    "Install fabric-pytester[keyvault] for Key Vault secrets"
                ) from exc
            self.credential = DefaultAzureCredential()
        try:
            SecretClient = import_module("azure.keyvault.secrets").SecretClient
        except ImportError as exc:  # pragma: no cover - import guard
            raise SecretError("Install fabric-pytester[keyvault] for Key Vault secrets") from exc
        self._client = SecretClient(vault_url=self.vault_url, credential=self.credential)

    def get(self, name: str, default: str | None = None) -> str | None:
        secret_name = self.names.get(name, name)
        try:
            return self._client.get_secret(secret_name).value
        except Exception:
            return default


@dataclass
class ChainedSecretProvider(SecretProvider):
    providers: list[SecretProvider]

    def get(self, name: str, default: str | None = None) -> str | None:
        for provider in self.providers:
            value = provider.get(name)
            if value not in (None, ""):
                return value
        return default


@dataclass
class AliasedSecretProvider(SecretProvider):
    provider: SecretProvider
    aliases: Mapping[str, str] = field(default_factory=dict)

    def get(self, name: str, default: str | None = None) -> str | None:
        value = self.provider.get(name)
        if value not in (None, ""):
            return value
        alias = self.aliases.get(name)
        if alias:
            value = self.provider.get(alias)
            if value not in (None, ""):
                return value
        return default


def env_or_skip(provider: SecretProvider, name: str) -> str:
    value = provider.get(name)
    if value in (None, ""):
        pytest.skip(f"Missing required Fabric test setting: {name}")
    return value
