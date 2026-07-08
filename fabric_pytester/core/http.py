from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import requests

from fabric_pytester.core.tokens import TokenProvider


@dataclass
class HttpResponse:
    status_code: int
    headers: dict[str, str]
    text: str
    data: Any = None

    def json(self) -> Any:
        return self.data


@dataclass
class BearerHttpClient:
    base_url: str
    token_provider: TokenProvider
    scope: str
    timeout: int = 60
    session: Any = field(default_factory=requests.Session)

    def _url(self, path_or_url: str) -> str:
        if path_or_url.startswith(("http://", "https://")):
            return path_or_url
        return f"{self.base_url.rstrip('/')}/{path_or_url.lstrip('/')}"

    def request(
        self,
        method: str,
        path_or_url: str,
        *,
        json: Any | None = None,
        data: Any | None = None,
        headers: dict[str, str] | None = None,
        expected: tuple[int, ...] = (200, 201, 202, 204),
        retry_unauthorized: bool = True,
        **kwargs: Any,
    ) -> HttpResponse:
        response = self._send(method, path_or_url, json=json, data=data, headers=headers, **kwargs)
        if response.status_code == 401 and retry_unauthorized:
            response = self._send(
                method,
                path_or_url,
                json=json,
                data=data,
                headers=headers,
                force_refresh=True,
                **kwargs,
            )
        if expected and response.status_code not in expected:
            message = (
                f"{method.upper()} {self._url(path_or_url)} returned "
                f"{response.status_code}: {response.text}"
            )
            raise requests.HTTPError(
                message,
                response=response,
            )
        return self._coerce_response(response)

    def _send(
        self,
        method: str,
        path_or_url: str,
        *,
        json: Any | None = None,
        data: Any | None = None,
        headers: dict[str, str] | None = None,
        force_refresh: bool = False,
        **kwargs: Any,
    ) -> Any:
        token = self.token_provider.get_token(self.scope, force_refresh=force_refresh)
        request_headers = {
            "Authorization": f"Bearer {token}",
            **(headers or {}),
        }
        return self.session.request(
            method.upper(),
            self._url(path_or_url),
            json=json,
            data=data,
            headers=request_headers,
            timeout=kwargs.pop("timeout", self.timeout),
            **kwargs,
        )

    @staticmethod
    def _coerce_response(response: Any) -> HttpResponse:
        data = None
        try:
            data = response.json()
        except ValueError:
            data = None
        return HttpResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            text=response.text,
            data=data,
        )

    def get_json(self, path_or_url: str, **kwargs: Any) -> Any:
        return self.request("GET", path_or_url, **kwargs).json()

    def post_json(
        self, path_or_url: str, payload: Any | None = None, **kwargs: Any
    ) -> HttpResponse:
        return self.request("POST", path_or_url, json=payload, **kwargs)
