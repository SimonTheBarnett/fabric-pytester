from __future__ import annotations

import logging
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from fabric_pytester.core.assertions import AssertionCollector, assert_absent, assert_rows
from fabric_pytester.core.onelake_client import OneLakePath

LOGGER = logging.getLogger(__name__)

STEP_RE = re.compile(
    r"^(?P<operation>insert|expected|absent|delete|run)_(?P<destination>[A-Za-z][A-Za-z0-9_]*)_(?P<order>\d+)$"
)


@dataclass(frozen=True, slots=True)
class DestinationStep:
    key: str
    operation: str
    destination: str
    order: int


class Destination(Protocol):
    def insert(self, target: str, payload: Mapping[str, Any]) -> Mapping[str, Any] | None: ...

    def expected(self, target: str, **kwargs: Any) -> None: ...

    def delete(self, target: str, **kwargs: Any) -> Mapping[str, Any] | None: ...


def parse_destination_step(key: str) -> DestinationStep | None:
    match = STEP_RE.match(key)
    if match is None:
        return None
    return DestinationStep(
        key=key,
        operation=match.group("operation"),
        destination=match.group("destination"),
        order=int(match.group("order")),
    )


class FabricDestination:
    def __init__(
        self,
        *,
        fabric_client: Any | None = None,
        fabric_sql: Any | None = None,
        default_sql_timeout_seconds: float = 300,
        default_sql_poll_interval_seconds: float = 10,
        debug: bool = False,
    ) -> None:
        self.fabric_client = fabric_client
        self.fabric_sql = fabric_sql
        self.default_sql_timeout_seconds = default_sql_timeout_seconds
        self.default_sql_poll_interval_seconds = default_sql_poll_interval_seconds
        self.debug = debug

    def insert(self, target: str, payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
        if target != "sql":
            raise ValueError("Fabric insert steps currently support target='sql'")
        if self.fabric_sql is None:
            raise ValueError("fabric_sql is not configured")
        self.fabric_sql.execute(payload["sql"])
        return None

    def expected(self, target: str, **kwargs: Any) -> None:
        return self.expected_with_context(
            target,
            scenario_key="fabric",
            block_key="expected_fabric",
            destination_name="fabric",
            operation="expected",
            **kwargs,
        )

    def expected_with_context(
        self,
        target: str,
        *,
        scenario_key: str,
        block_key: str,
        destination_name: str,
        operation: str,
        placeholders: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        if target != "sql":
            raise ValueError("Fabric expected steps currently support target='sql'")
        if self.fabric_sql is None:
            raise ValueError("fabric_sql is not configured")
        sql = kwargs["sql"]
        rows, elapsed = _poll_sql(
            self.fabric_sql,
            sql=sql,
            expected_count=kwargs.get("expected_count"),
            timeout_seconds=float(kwargs.get("timeout_seconds", self.default_sql_timeout_seconds)),
            poll_interval_seconds=float(
                kwargs.get("poll_interval_seconds", self.default_sql_poll_interval_seconds)
            ),
            scenario_key=scenario_key,
            block_key=block_key,
            debug=self.debug,
        )
        collector = AssertionCollector()
        assert_rows(
            scenario=destination_name,
            target=target,
            rows=rows,
            expected_count=kwargs.get("expected_count"),
            fields=kwargs.get("fields"),
            collector=collector,
        )
        if collector.failures:
            raise AssertionError(
                "; ".join(collector.failures)
                + "; "
                + _sql_failure_details(
                    sql,
                    kwargs.get("expected_count"),
                    rows,
                    elapsed,
                    self.fabric_sql,
                    placeholders,
                )
            )

    def absent(self, target: str, **kwargs: Any) -> None:
        return self.absent_with_context(
            target,
            scenario_key="fabric",
            block_key="absent_fabric",
            destination_name="fabric",
            operation="absent",
            **kwargs,
        )

    def absent_with_context(
        self,
        target: str,
        *,
        scenario_key: str,
        block_key: str,
        destination_name: str,
        operation: str,
        placeholders: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        if target != "sql":
            raise ValueError("Fabric absent steps currently support target='sql'")
        if self.fabric_sql is None:
            raise ValueError("fabric_sql is not configured")
        sql = kwargs["sql"]
        rows, elapsed = _poll_sql(
            self.fabric_sql,
            sql=sql,
            expected_count=0,
            timeout_seconds=float(kwargs.get("timeout_seconds", self.default_sql_timeout_seconds)),
            poll_interval_seconds=float(
                kwargs.get("poll_interval_seconds", self.default_sql_poll_interval_seconds)
            ),
            scenario_key=scenario_key,
            block_key=block_key,
            debug=self.debug,
        )
        collector = AssertionCollector()
        assert_absent(scenario=destination_name, target=target, rows=rows, collector=collector)
        if collector.failures:
            raise AssertionError(
                "; ".join(collector.failures)
                + "; "
                + _sql_failure_details(sql, 0, rows, elapsed, self.fabric_sql, placeholders)
            )

    def delete(self, target: str, **kwargs: Any) -> Mapping[str, Any] | None:
        if target != "sql":
            raise ValueError("Fabric delete steps currently support target='sql'")
        if self.fabric_sql is None:
            raise ValueError("fabric_sql is not configured")
        self.fabric_sql.execute(kwargs["sql"])
        return None

    def run(self, target: str, **kwargs: Any) -> Any:
        if self.fabric_client is None:
            raise ValueError("fabric_client is not configured")
        run = self.fabric_client.run_item_job(
            item_id=kwargs.get("item_id"),
            item_name=kwargs.get("item") or target,
            job_type=str(kwargs.get("job_type", "Pipeline")),
            parameters=kwargs.get("parameters"),
        )
        status = run.wait()
        _debug_print(
            self.debug,
            f"Fabric job instance_id={getattr(run, 'instance_id', '<unknown>')} "
            f"item_id={getattr(run, 'item_id', '<unknown>')} "
            f"status={status.get('status') or status.get('state')}",
        )
        return run


class SqlBackendDestination:
    def __init__(
        self,
        backend: Any,
        *,
        default_timeout_seconds: float = 300,
        default_poll_interval_seconds: float = 10,
        debug: bool = False,
    ) -> None:
        self.backend = backend
        self.default_timeout_seconds = default_timeout_seconds
        self.default_poll_interval_seconds = default_poll_interval_seconds
        self.debug = debug

    def insert(self, target: str, payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
        if target != "sql":
            raise ValueError("SQL backend insert steps support target='sql'")
        self.backend.execute(payload["sql"])
        return None

    def expected(self, target: str, **kwargs: Any) -> None:
        return self.expected_with_context(
            target,
            scenario_key="sql",
            block_key="expected_sql",
            destination_name="sql",
            operation="expected",
            **kwargs,
        )

    def expected_with_context(
        self,
        target: str,
        *,
        scenario_key: str,
        block_key: str,
        destination_name: str,
        operation: str,
        placeholders: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        if target != "sql":
            raise ValueError("SQL backend expected steps support target='sql'")
        sql = kwargs["sql"]
        rows, elapsed = _poll_sql(
            self.backend,
            sql=sql,
            expected_count=kwargs.get("expected_count"),
            timeout_seconds=float(kwargs.get("timeout_seconds", self.default_timeout_seconds)),
            poll_interval_seconds=float(
                kwargs.get("poll_interval_seconds", self.default_poll_interval_seconds)
            ),
            scenario_key=scenario_key,
            block_key=block_key,
            debug=self.debug,
        )
        collector = AssertionCollector()
        assert_rows(
            scenario=destination_name,
            target=target,
            rows=rows,
            expected_count=kwargs.get("expected_count"),
            fields=kwargs.get("fields"),
            collector=collector,
        )
        if collector.failures:
            raise AssertionError(
                "; ".join(collector.failures)
                + "; "
                + _sql_failure_details(
                    sql,
                    kwargs.get("expected_count"),
                    rows,
                    elapsed,
                    self.backend,
                    placeholders,
                )
            )

    def absent(self, target: str, **kwargs: Any) -> None:
        return self.absent_with_context(
            target,
            scenario_key="sql",
            block_key="absent_sql",
            destination_name="sql",
            operation="absent",
            **kwargs,
        )

    def absent_with_context(
        self,
        target: str,
        *,
        scenario_key: str,
        block_key: str,
        destination_name: str,
        operation: str,
        placeholders: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        if target != "sql":
            raise ValueError("SQL backend absent steps support target='sql'")
        sql = kwargs["sql"]
        rows, elapsed = _poll_sql(
            self.backend,
            sql=sql,
            expected_count=0,
            timeout_seconds=float(kwargs.get("timeout_seconds", self.default_timeout_seconds)),
            poll_interval_seconds=float(
                kwargs.get("poll_interval_seconds", self.default_poll_interval_seconds)
            ),
            scenario_key=scenario_key,
            block_key=block_key,
            debug=self.debug,
        )
        collector = AssertionCollector()
        assert_absent(scenario=destination_name, target=target, rows=rows, collector=collector)
        if collector.failures:
            raise AssertionError(
                "; ".join(collector.failures)
                + "; "
                + _sql_failure_details(sql, 0, rows, elapsed, self.backend, placeholders)
            )

    def delete(self, target: str, **kwargs: Any) -> Mapping[str, Any] | None:
        if target != "sql":
            raise ValueError("SQL backend delete steps support target='sql'")
        self.backend.execute(kwargs["sql"])
        return None

    def close(self) -> None:
        close = getattr(self.backend, "close", None)
        if callable(close):
            close()


class OneLakeDestination:
    def __init__(self, client: Any, *, debug: bool = False) -> None:
        self.client = client
        self.debug = debug

    def insert(self, target: str, payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
        if target != "file":
            raise ValueError("OneLake insert steps currently support target='file'")
        uploaded = self.client.upload(
            folder=payload["folder"],
            filename=payload["filename"],
            content=payload.get("content"),
            records=payload.get("records"),
            overwrite=bool(payload.get("overwrite", True)),
        )
        path = uploaded.path
        _debug_print(
            self.debug,
            f"Uploaded OneLake filename={payload['filename']} path={path.file_system}/{path.path}",
        )
        return {
            "path": path.path,
            "_cleanup": (f"OneLake {path.path}", lambda path=path: self.client.delete(path)),
        }

    def expected(self, target: str, **kwargs: Any) -> None:
        if target != "file":
            raise ValueError("OneLake expected steps currently support target='file'")
        content = self.client.download_latest(kwargs["folder"], kwargs.get("pattern"))
        if "contains" in kwargs and str(kwargs["contains"]).encode("utf-8") not in content:
            raise AssertionError(f"file did not contain {kwargs['contains']!r}")

    def delete(self, target: str, **kwargs: Any) -> Mapping[str, Any] | None:
        path = _onelake_path(self.client, target, kwargs)
        self.client.delete(path)
        return None


def _onelake_path(client: Any, target: str, kwargs: Mapping[str, Any]) -> OneLakePath:
    path = kwargs.get("path")
    if isinstance(path, OneLakePath):
        return path
    if isinstance(path, str):
        return OneLakePath(client.workspace, path)
    if "folder" in kwargs and "filename" in kwargs:
        return OneLakePath.from_parts(
            client.workspace, client.lakehouse_root, kwargs["folder"], kwargs["filename"]
        )
    if target != "file":
        return OneLakePath(client.workspace, target)
    raise ValueError("OneLake delete requires path or folder and filename")


def _poll_sql(
    backend: Any,
    *,
    sql: str,
    expected_count: int | None,
    timeout_seconds: float,
    poll_interval_seconds: float,
    scenario_key: str,
    block_key: str,
    debug: bool = False,
) -> tuple[list[Any], float]:
    started = time.monotonic()
    deadline = started + timeout_seconds
    rows: list[Any] = []
    while True:
        rows = backend.fetch_all(sql)
        elapsed = time.monotonic() - started
        actual_count = len(rows)
        LOGGER.info(
            "SQL assertion poll scenario=%s block=%s expected_count=%s actual_count=%s "
            "elapsed=%.1fs sql=%s",
            scenario_key,
            block_key,
            expected_count if expected_count is not None else "<any>",
            actual_count,
            elapsed,
            _short_sql(sql),
        )
        info = getattr(backend, "info", None)
        _debug_print(
            debug,
            f"SQL assertion scenario={scenario_key} block={block_key} "
            f"adapter={getattr(info, 'adapter', '<unknown>')} "
            f"host={getattr(info, 'host', None)} database={getattr(info, 'database', None)} "
            f"expected_count={expected_count if expected_count is not None else '<any>'} "
            f"actual_count={actual_count} elapsed={elapsed:.1f}s sql={_short_sql(sql)} "
            f"sample_rows={rows[:3]!r}",
        )
        if expected_count is None and rows:
            return rows, elapsed
        if expected_count is not None and actual_count == expected_count:
            return rows, elapsed
        if time.monotonic() >= deadline:
            return rows, elapsed
        time.sleep(poll_interval_seconds)


def _sql_failure_details(
    sql: str,
    expected_count: Any,
    rows: list[Any],
    elapsed: float,
    backend: Any = None,
    placeholders: Mapping[str, Any] | None = None,
    sample_size: int = 3,
) -> str:
    info = getattr(backend, "info", None)
    return (
        f"adapter={getattr(info, 'adapter', '<unknown>')!r}; "
        f"sql_host={getattr(info, 'host', None)!r}; "
        f"sql_database={getattr(info, 'database', None)!r}; "
        f"sql={sql!r}; expected_count={expected_count!r}; "
        f"actual_count={len(rows)}; elapsed={elapsed:.1f}s; "
        f"placeholders={dict(placeholders or {})!r}; sample_rows={rows[:sample_size]!r}"
    )


def _short_sql(sql: str, limit: int = 180) -> str:
    normalized = " ".join(str(sql).split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."


def _debug_print(enabled: bool, message: str) -> None:
    if enabled:
        print(f"[fabric-pytester] {message}")
