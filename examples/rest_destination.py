from __future__ import annotations

from typing import Any

import requests


class RestDestination:
    def __init__(self, base_url: str, headers: dict[str, str] | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = headers or {}

    def insert(self, target: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        response = requests.request(
            payload.get("method", "POST"),
            f"{self.base_url}/{target.lstrip('/')}",
            headers=self.headers,
            json=payload.get("json"),
            data=payload.get("data"),
            timeout=payload.get("timeout", 30),
        )
        response.raise_for_status()
        return response.json() if _is_json(response) else None

    def expected(self, target: str, **kwargs: Any) -> None:
        response = requests.request(
            kwargs.get("method", "GET"),
            f"{self.base_url}/{target.lstrip('/')}",
            headers=self.headers,
            timeout=kwargs.get("timeout", 30),
        )
        expected_status = kwargs.get("status", [200])
        if isinstance(expected_status, int):
            expected_status = [expected_status]
        if response.status_code not in expected_status:
            raise AssertionError(f"expected status {expected_status}, got {response.status_code}")
        if "contains" in kwargs and kwargs["contains"] not in response.text:
            raise AssertionError(f"response did not contain {kwargs['contains']!r}")

    def delete(self, target: str, **kwargs: Any) -> None:
        response = requests.delete(
            f"{self.base_url}/{target.lstrip('/')}",
            headers=self.headers,
            timeout=kwargs.get("timeout", 30),
        )
        response.raise_for_status()


def _is_json(response: requests.Response) -> bool:
    return "application/json" in response.headers.get("content-type", "")
