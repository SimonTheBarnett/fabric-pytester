import pytest

SCENARIOS = [
    "returns_dataverse_to_fabric",
    "returns_jdbc_source_to_fabric",
]


@pytest.mark.fabric_scenarios(SCENARIOS)
def test_returns_pipeline(project_runner):
    project_runner.run_scenarios(SCENARIOS)
