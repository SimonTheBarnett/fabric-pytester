class FabricPytesterError(Exception):
    """Base error for fabric-pytester."""


class ConfigError(FabricPytesterError):
    """Raised when configuration cannot be loaded or validated."""


class SecretError(FabricPytesterError):
    """Raised when a required secret is unavailable."""


class FabricJobError(FabricPytesterError):
    """Raised when a Fabric item job fails or times out."""


class ScenarioError(FabricPytesterError):
    """Raised when scenarios cannot be loaded, rendered, or executed."""


class AssertionGroupError(AssertionError):
    """Assertion error that groups failures by scenario and target."""

    def __init__(self, failures: list[str]) -> None:
        self.failures = failures
        super().__init__("\n".join(failures))
