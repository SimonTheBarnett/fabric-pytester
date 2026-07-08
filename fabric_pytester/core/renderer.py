from __future__ import annotations

import datetime as dt
import random
import re
import string
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

PLACEHOLDER_RE = re.compile(r"\{([A-Za-z0-9_]+)\}")
RANDOM_PLACEHOLDER_RE = re.compile(
    r"^random_(?P<kind>numbers|alpha|alpha_numeric)_(?P<length>[1-9]\d*)$"
)
GENERATED_PLACEHOLDER_RE = re.compile(
    r"^generated_(?P<name>[A-Za-z][A-Za-z0-9_]*?)_(?P<kind>numbers|alpha|alpha_numeric)_(?P<length>[1-9]\d*)$"
)
PlaceholderProvider = Callable[["ScenarioContext"], Mapping[str, Any]]


@dataclass
class MappingPlaceholderProvider:
    values: Mapping[str, Any] = field(default_factory=dict)

    def __call__(self, context: ScenarioContext) -> Mapping[str, Any]:
        return dict(self.values)


@dataclass
class RandomHelper:
    alphabet: str = string.ascii_lowercase + string.digits

    def __call__(self, prefix: str = "test", length: int = 8) -> str:
        return f"{prefix}_{self.alphanumeric(length)}"

    def digits(self, length: int) -> str:
        return "".join(random.choice(string.digits) for _ in range(length))

    def alpha_numeric(self, length: int) -> str:
        chars = string.ascii_uppercase + string.digits
        return "".join(random.choice(chars) for _ in range(length))

    def alpha(self, length: int) -> str:
        return "".join(random.choice(string.ascii_uppercase) for _ in range(length))

    def alphanumeric(self, length: int) -> str:
        return "".join(random.choice(self.alphabet) for _ in range(length))


@dataclass
class ScenarioContext:
    scenario_key: str
    variables: dict[str, Any] = field(default_factory=dict)
    captures: dict[str, Any] = field(default_factory=dict)
    random: RandomHelper = field(default_factory=RandomHelper)
    providers: list[PlaceholderProvider] = field(default_factory=list)

    def values(self) -> dict[str, Any]:
        now = dt.datetime.now(dt.UTC)
        values: dict[str, Any] = {
            "scenario_key": self.scenario_key,
            "run_id": self.variables.setdefault("run_id", uuid.uuid4().hex),
            "uuid": self.variables.setdefault("uuid", str(uuid.uuid4())),
            "current_date": dt.date.today().isoformat(),
            "current_timestamp": now.isoformat(),
        }
        values.update(self.variables)
        values.update(self.captures)
        for provider in self.providers:
            values.update(provider(self))
        return values

    def resolve_placeholder(self, key: str) -> Any:
        if key in self.variables:
            return self.variables[key]
        match = RANDOM_PLACEHOLDER_RE.match(key) or GENERATED_PLACEHOLDER_RE.match(key)
        if not match:
            return None
        length = int(match.group("length"))
        kind = match.group("kind")
        if kind == "numbers":
            value = self.random.digits(length)
        elif kind == "alpha":
            value = self.random.alpha(length)
        else:
            value = self.random.alpha_numeric(length)
        self.variables[key] = value
        return value

    def capture(self, name: str, value: Any) -> None:
        self.captures[name] = value


def render(value: Any, context: ScenarioContext) -> Any:
    values = context.values()
    if isinstance(value, str):

        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            if key not in values:
                resolved = context.resolve_placeholder(key)
                if resolved is None:
                    return match.group(0)
                values[key] = resolved
            return str(values[key])

        return PLACEHOLDER_RE.sub(replace, value)
    if isinstance(value, list):
        return [render(item, context) for item in value]
    if isinstance(value, dict):
        return {key: render(item, context) for key, item in value.items()}
    return value
