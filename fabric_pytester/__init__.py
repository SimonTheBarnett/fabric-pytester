"""Reusable pytest helpers for Microsoft Fabric integration tests."""

from importlib.metadata import PackageNotFoundError, version

from fabric_pytester.core.runner import ScenarioRunner

try:
    __version__ = version("fabric-pytester")
except PackageNotFoundError:  # pragma: no cover - source tree without installed metadata
    __version__ = "0+unknown"

__all__ = ["ScenarioRunner", "__version__"]
