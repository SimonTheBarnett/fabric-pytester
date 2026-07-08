from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class CleanupHandle:
    name: str
    cleanup: Callable[[], None]

    def run(self) -> None:
        self.cleanup()


@dataclass
class CleanupStack:
    handles: list[CleanupHandle] = field(default_factory=list)

    def add(self, name: str, cleanup: Callable[[], None]) -> CleanupHandle:
        handle = CleanupHandle(name=name, cleanup=cleanup)
        self.handles.append(handle)
        return handle

    def run(self) -> list[str]:
        errors: list[str] = []
        for handle in reversed(self.handles):
            try:
                handle.run()
            except Exception as exc:
                errors.append(f"{handle.name}: {exc}")
        self.handles.clear()
        return errors
