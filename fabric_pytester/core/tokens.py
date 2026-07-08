from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from fabric_pytester.core.credentials import SecretProvider

FABRIC_SCOPE = "https://analysis.windows.net/powerbi/api/.default"
ONELAKE_SCOPE = "https://storage.azure.com/.default"


class TokenProvider(Protocol):
    def get_token(self, scope: str, *, force_refresh: bool = False) -> str: ...


def token_value(value: Any) -> str:
    token = getattr(value, "token", value)
    return str(token)


@dataclass(slots=True)
class CachedToken:
    token: str
    expires_at: float

    def fresh(self, *, now: float | None = None, skew_seconds: int = 300) -> bool:
        return self.expires_at - skew_seconds > (now or time.time())


@dataclass
class ClientCredentialsTokenProvider:
    secrets: SecretProvider
    credential: Any | None = None
    cache: dict[str, CachedToken] = field(default_factory=dict)

    def _credential(self) -> Any:
        if self.credential is None:
            from azure.identity import ClientSecretCredential

            self.credential = ClientSecretCredential(
                tenant_id=self.secrets.require("FABRIC_TENANT_ID"),
                client_id=self.secrets.require("FABRIC_CLIENT_ID"),
                client_secret=self.secrets.require("FABRIC_CLIENT_SECRET"),
            )
        return self.credential

    def get_token(self, scope: str, *, force_refresh: bool = False) -> str:
        cached = self.cache.get(scope)
        if cached and not force_refresh and cached.fresh():
            return cached.token
        token = self._credential().get_token(scope)
        value = token.token
        expires_on = float(getattr(token, "expires_on", time.time() + 3600))
        self.cache[scope] = CachedToken(value, expires_on)
        return value


@dataclass
class ClientCredentialsUrlTokenProvider:
    secrets: SecretProvider
    auth_url: str
    client_id_secret: str = "FABRIC_CLIENT_ID"
    client_secret_secret: str = "FABRIC_CLIENT_SECRET"
    grant_type: str = "client_credentials"
    session: Any | None = None
    cache: dict[str, CachedToken] = field(default_factory=dict)

    def get_token(self, scope: str, *, force_refresh: bool = False) -> str:
        cached = self.cache.get(scope)
        if cached and not force_refresh and cached.fresh():
            return cached.token
        session = self.session
        if session is None:
            import requests

            session = requests
        response = session.post(
            self.auth_url,
            data={
                "grant_type": self.grant_type,
                "client_id": self.secrets.require(self.client_id_secret),
                "client_secret": self.secrets.require(self.client_secret_secret),
                "scope": scope,
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        value = str(payload["access_token"])
        expires_in = float(payload.get("expires_in", 3600))
        self.cache[scope] = CachedToken(value, time.time() + expires_in)
        return value


@dataclass
class StaticTokenProvider:
    token: str = "test-token"

    def get_token(self, scope: str, *, force_refresh: bool = False) -> str:
        return self.token


@dataclass
class ExistingTokenProvider:
    source: Any

    def get_token(self, scope: str, *, force_refresh: bool = False) -> str:
        if hasattr(self.source, "get_token"):
            try:
                return token_value(self.source.get_token(scope, force_refresh=force_refresh))
            except TypeError:
                return token_value(self.source.get_token(scope))
        if callable(self.source):
            try:
                return token_value(self.source(scope, force_refresh=force_refresh))
            except TypeError:
                try:
                    return token_value(self.source(scope))
                except TypeError:
                    return token_value(self.source())
        return token_value(self.source)


def coerce_token_provider(value: Any) -> TokenProvider:
    return ExistingTokenProvider(value)
