from __future__ import annotations

from collections.abc import Sequence
from typing import Any


class SqlDestination:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def insert(self, target: str, payload: dict[str, Any]) -> None:
        self._execute(payload["sql"], payload.get("params", ()))

    def expected(self, target: str, **kwargs: Any) -> None:
        rows = self._fetch_all(kwargs["sql"], kwargs.get("params", ()))
        expected_count = kwargs.get("expected_count")
        if expected_count is not None and len(rows) != expected_count:
            raise AssertionError(f"expected {expected_count} row(s), got {len(rows)}")
        fields = kwargs.get("fields", {})
        if fields and not rows:
            raise AssertionError("expected rows for field comparison, got none")
        for field, expected in fields.items():
            actual = rows[0].get(field)
            if actual != expected:
                raise AssertionError(f"{field}: expected {expected!r}, got {actual!r}")

    def delete(self, target: str, **kwargs: Any) -> None:
        self._execute(kwargs["sql"], kwargs.get("params", ()))

    def _execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        cursor = self.connection.cursor()
        cursor.execute(sql, params)
        self.connection.commit()

    def _fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        cursor = self.connection.cursor()
        cursor.execute(sql, params)
        columns = [column[0] for column in cursor.description or []]
        return [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]
