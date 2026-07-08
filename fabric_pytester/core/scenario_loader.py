from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fabric_pytester.core.destinations import DestinationStep, parse_destination_step
from fabric_pytester.core.errors import ScenarioError

BLOCK_RE = re.compile(r"^(?P<name>[A-Za-z_]+?)(?:_(?P<number>\d+))?$")


@dataclass(slots=True)
class Scenario:
    key: str
    data: dict[str, Any]
    source: Path

    @property
    def variables(self) -> dict[str, Any]:
        return dict(self.data.get("variables", {}))

    def blocks(self, prefix: str) -> list[tuple[str, dict[str, Any]]]:
        selected: list[tuple[int, str, dict[str, Any]]] = []
        for key, value in self.data.items():
            if key == prefix or key.startswith(f"{prefix}_"):
                number = _block_number(key)
                selected.append((number, key, value))
        return [(key, value) for _, key, value in sorted(selected)]

    def destination_blocks(self, operation: str) -> list[tuple[DestinationStep, dict[str, Any]]]:
        selected: list[tuple[int, str, DestinationStep, dict[str, Any]]] = []
        for key, value in self.data.items():
            step = parse_destination_step(key)
            if step is not None and step.operation == operation:
                selected.append((step.order, key, step, value))
        return [(step, value) for _, _, step, value in sorted(selected)]


@dataclass
class ScenarioLoader:
    paths: list[Path]

    def load(self) -> dict[str, Scenario]:
        scenarios: dict[str, Scenario] = {}
        for file_path in self._files():
            with file_path.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
            if not isinstance(raw, dict):
                raise ScenarioError(f"Scenario file must contain a JSON object: {file_path}")
            for key, value in raw.items():
                if key in scenarios:
                    first = scenarios[key].source
                    raise ScenarioError(f"Duplicate scenario key {key!r}: {first} and {file_path}")
                if not isinstance(value, dict):
                    raise ScenarioError(f"Scenario {key!r} must be an object in {file_path}")
                scenarios[key] = Scenario(key=key, data=value, source=file_path)
        return scenarios

    def _files(self) -> list[Path]:
        files: list[Path] = []
        for path in self.paths:
            if path.is_dir():
                files.extend(sorted(path.glob("*.json")))
            elif path.exists():
                files.append(path)
            else:
                raise ScenarioError(f"Scenario path does not exist: {path}")
        return files


def _block_number(key: str) -> int:
    match = BLOCK_RE.match(key)
    if not match or match.group("number") is None:
        return 0
    return int(match.group("number"))
