from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from fabric_pytester.core.tokens import TokenProvider


class Row(dict[str, Any]):
    def get_ci(self, key: str, default: Any = None) -> Any:
        lowered = key.lower()
        for existing, value in self.items():
            if existing.lower() == lowered:
                return value
        return default


@dataclass(frozen=True, slots=True)
class SqlBackendInfo:
    adapter: str
    host: str | None = None
    database: str | None = None


class SqlBackend(ABC):
    info: SqlBackendInfo | None = None

    @abstractmethod
    def execute(self, sql: str, params: Sequence[Any] = ()) -> None: ...

    @abstractmethod
    def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> Row | None: ...

    @abstractmethod
    def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[Row]: ...

    def close(self) -> None:
        return None

    def poll(
        self,
        sql: str,
        *,
        expected_count: int | None = None,
        timeout_seconds: float = 300,
        poll_interval_seconds: float = 5,
    ) -> list[Row]:
        deadline = time.monotonic() + timeout_seconds
        rows: list[Row] = []
        while time.monotonic() < deadline:
            rows = self.fetch_all(sql)
            if expected_count is None and rows:
                return rows
            if expected_count is not None and len(rows) == expected_count:
                return rows
            time.sleep(poll_interval_seconds)
        return rows


BackendFactory = Callable[[Mapping[str, Any], Mapping[str, str], TokenProvider | None], SqlBackend]


@dataclass
class SqlBackendRegistry:
    factories: dict[str, BackendFactory] = field(default_factory=dict)

    def register(self, adapter: str, factory: BackendFactory) -> None:
        self.factories[adapter] = factory

    def create(
        self,
        name: str,
        config: Mapping[str, Any],
        secrets: Mapping[str, str] | None = None,
        token_provider: TokenProvider | None = None,
    ) -> SqlBackend:
        adapter = str(config.get("adapter") or config.get("sql_adapter") or name)
        if adapter not in self.factories:
            raise KeyError(f"No SQL backend factory registered for adapter {adapter!r}")
        return self.factories[adapter](config, secrets or {}, token_provider)


class DbApiSqlBackend(SqlBackend):
    def __init__(
        self,
        connection: Any,
        *,
        query_timeout_seconds: int | None = None,
        info: SqlBackendInfo | None = None,
    ) -> None:
        self.connection = connection
        self.query_timeout_seconds = query_timeout_seconds
        self.info = info
        self._enable_autocommit()

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        cursor = self.connection.cursor()
        try:
            self._apply_query_timeout(cursor)
            cursor.execute(sql, params)
            self._commit_if_available()
        finally:
            close = getattr(cursor, "close", None)
            if callable(close):
                close()

    def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> Row | None:
        rows = self.fetch_all(sql, params)
        return rows[0] if rows else None

    def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[Row]:
        cursor = self.connection.cursor()
        try:
            self._apply_query_timeout(cursor)
            cursor.execute(sql, params)
            columns = [column[0] for column in cursor.description or []]
            return [Row(zip(columns, row, strict=False)) for row in cursor.fetchall()]
        finally:
            close = getattr(cursor, "close", None)
            if callable(close):
                close()

    def close(self) -> None:
        close = getattr(self.connection, "close", None)
        if callable(close):
            close()

    def _apply_query_timeout(self, cursor: Any) -> None:
        if self.query_timeout_seconds is None:
            return
        try:
            cursor.timeout = self.query_timeout_seconds
        except Exception:
            return

    def _enable_autocommit(self) -> None:
        for target in (self.connection, getattr(self.connection, "jconn", None)):
            if target is None:
                continue
            setter = getattr(target, "setAutoCommit", None)
            if callable(setter):
                with suppress(Exception):
                    setter(True)
            with suppress(Exception):
                target.autocommit = True

    def _commit_if_available(self) -> None:
        commit = getattr(self.connection, "commit", None)
        if not callable(commit):
            return
        try:
            commit()
        except Exception:
            return


class LazySqlBackend(SqlBackend):
    def __init__(self, factory: Callable[[], SqlBackend]) -> None:
        self._factory = factory
        self._backend: SqlBackend | None = None

    @property
    def info(self) -> SqlBackendInfo | None:
        return self.backend.info

    @property
    def backend(self) -> SqlBackend:
        if self._backend is None:
            self._backend = self._factory()
        return self._backend

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        self.backend.execute(sql, params)

    def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> Row | None:
        return self.backend.fetch_one(sql, params)

    def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[Row]:
        return self.backend.fetch_all(sql, params)

    def close(self) -> None:
        if self._backend is not None:
            self._backend.close()
            self._backend = None


def default_registry() -> SqlBackendRegistry:
    from fabric_pytester.adapters.jdbc import create_jdbc_backend
    from fabric_pytester.adapters.jdbc_token import create_fabric_jdbc_token_backend
    from fabric_pytester.adapters.mssql_python import create_mssql_python_backend
    from fabric_pytester.adapters.pyodbc import create_pyodbc_backend

    registry = SqlBackendRegistry()
    registry.register("mssql-python", create_mssql_python_backend)
    registry.register("fabric", create_mssql_python_backend)
    registry.register("fabric-jdbc-token", create_fabric_jdbc_token_backend)
    registry.register("pyodbc", create_pyodbc_backend)
    registry.register("jdbc", create_jdbc_backend)
    return registry
