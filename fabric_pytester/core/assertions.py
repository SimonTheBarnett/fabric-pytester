from __future__ import annotations

import datetime as dt
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from fabric_pytester.core.errors import AssertionGroupError
from fabric_pytester.core.sql_backend import Row


@dataclass
class AssertionCollector:
    failures: list[str] = field(default_factory=list)

    def add(self, scenario: str, target: str, message: str) -> None:
        self.failures.append(f"[{scenario}] {target}: {message}")

    def raise_if_any(self) -> None:
        if self.failures:
            raise AssertionGroupError(self.failures)


def assert_rows(
    *,
    scenario: str,
    target: str,
    rows: list[Row],
    expected_count: int | None = None,
    fields: dict[str, Any] | None = None,
    collector: AssertionCollector | None = None,
) -> None:
    collector = collector or AssertionCollector()
    if expected_count is not None and len(rows) != expected_count:
        collector.add(scenario, target, f"expected {expected_count} row(s), got {len(rows)}")
        return
    if fields and not rows:
        collector.add(scenario, target, "expected rows for field comparison, got none")
        return
    if fields:
        row = rows[0]
        for field, expected in fields.items():
            actual = row.get_ci(field)
            if not value_matches(actual, expected):
                collector.add(
                    scenario,
                    target,
                    f"{field}: expected {_display_value(expected)}, got {_display_value(actual)}",
                )


def assert_absent(
    *, scenario: str, target: str, rows: list[Row], collector: AssertionCollector
) -> None:
    if rows:
        collector.add(scenario, target, f"expected no rows, got {len(rows)}")


def value_matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, Mapping):
        return dict_matches(actual, expected)
    if expected == "{NULL}":
        return actual is None
    if expected == "{!NULL}":
        return actual is not None
    if expected == "{UUID}":
        try:
            uuid.UUID(str(actual))
            return True
        except ValueError:
            return False
    if expected == "{DATE}":
        try:
            dt.date.fromisoformat(str(actual)[:10])
            return True
        except ValueError:
            return False
    if isinstance(expected, str) and expected.startswith("{CONTAINS:") and expected.endswith("}"):
        needle = expected.removeprefix("{CONTAINS:").removesuffix("}")
        return needle in str(actual)
    if isinstance(expected, str) and expected.startswith("{REGEX:") and expected.endswith("}"):
        pattern = expected.removeprefix("{REGEX:").removesuffix("}")
        return re.search(pattern, str(actual)) is not None
    return _normalize_for_equality(actual, expected) == _normalize_for_equality(expected, actual)


def dict_matches(actual: Any, expected: Mapping[str, Any]) -> bool:
    if "equals" in expected and not value_matches(actual, expected["equals"]):
        return False
    if "contains" in expected:
        needle = str(expected["contains"])
        haystack = str(actual)
        if needle not in haystack:
            return False
        if "count" in expected and haystack.count(needle) != int(expected["count"]):
            return False
    elif "count" in expected:
        try:
            if len(actual) != int(expected["count"]):
                return False
        except TypeError:
            return False
    return "regex" not in expected or re.search(str(expected["regex"]), str(actual)) is not None


def _normalize_for_equality(value: Any, other: Any) -> Any:
    if isinstance(value, dt.datetime):
        if _is_date_only_value(other):
            return value.date().isoformat()
        return value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, str) and isinstance(other, dt.date):
        try:
            parsed = dt.date.fromisoformat(value[:10])
        except ValueError:
            return value
        if value == parsed.isoformat() or value.startswith(f"{parsed.isoformat()}T"):
            return parsed.isoformat() if _is_date_only_value(other) else value
    return value


def _is_date_like_value(value: Any) -> bool:
    return isinstance(value, dt.date) or _is_iso_date_string(value)


def _is_date_only_value(value: Any) -> bool:
    return (
        isinstance(value, dt.date)
        and not isinstance(value, dt.datetime)
        or _is_iso_date_string(value)
    )


def _is_iso_date_string(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _display_value(value: Any) -> str:
    normalized = _normalize_for_equality(value, value)
    return str(normalized)
