from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from fabric_pytester.core.http import BearerHttpClient

ENTITY_ID_RE = re.compile(r"\(([^)]+)\)")


@dataclass
class DataverseDestination:
    http: BearerHttpClient
    endpoint_prefix: str = "/api/data/v9.2"
    payload_builders: dict[str, Any] = field(default_factory=dict)

    def insert(self, target: str, payload: dict[str, Any]) -> dict[str, Any]:
        builder = self.payload_builders.get(target)
        if builder is not None:
            payload = builder(payload)
        response = self.http.post_json(self._path(target), payload)
        result = response.json() if isinstance(response.json(), dict) else {}
        entity_id = entity_id_from_headers(response.headers)
        if entity_id:
            result["@entity_id"] = entity_id
        return result

    def expected(self, target: str, **kwargs: Any) -> None:
        rows = self.query(target, kwargs.get("filter"))
        expected_count = kwargs.get("expected_count")
        if expected_count is not None and len(rows) != expected_count:
            raise AssertionError(f"expected {expected_count} row(s), got {len(rows)}")
        fields = kwargs.get("fields", {})
        if fields and not rows:
            raise AssertionError("expected rows for field comparison, got none")
        for field_name, expected in fields.items():
            actual = rows[0].get(field_name)
            if actual != expected:
                raise AssertionError(f"{field_name}: expected {expected!r}, got {actual!r}")

    def delete(self, target: str, **kwargs: Any) -> dict[str, Any]:
        deleted = self.delete_by_filter(target, kwargs["filter"])
        return {"deleted": deleted}

    def query(self, entity_set: str, filter_expression: str | None = None) -> list[dict[str, Any]]:
        suffix = f"?$filter={filter_expression}" if filter_expression else ""
        payload = self.http.get_json(f"{self._path(entity_set)}{suffix}")
        return list(payload.get("value", []))

    def delete_by_filter(self, entity_set: str, filter_expression: str) -> int:
        rows = self.query(entity_set, filter_expression)
        for row in rows:
            entity_id = row.get("@entity_id") or row.get("id") or row.get(f"{entity_set}id")
            if entity_id:
                self.http.request(
                    "DELETE", f"{self._path(entity_set)}({entity_id})", expected=(200, 202, 204)
                )
        return len(rows)

    def _path(self, entity_set: str) -> str:
        return f"{self.endpoint_prefix.rstrip('/')}/{entity_set.lstrip('/')}".lstrip("/")


def entity_id_from_headers(headers: dict[str, str]) -> str | None:
    location = (
        headers.get("OData-EntityId") or headers.get("odata-entityid") or headers.get("Location")
    )
    if not location:
        return None
    match = ENTITY_ID_RE.search(location)
    return match.group(1) if match else location.rstrip("/").split("/")[-1]
