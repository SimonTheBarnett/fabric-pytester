import pytest

SCENARIOS = [
    "orders_dataverse_to_fabric",
    "orders_jdbc_source_to_fabric",
]


class TestOrdersPipeline:
    @pytest.mark.fabric_scenarios(SCENARIOS)
    def test_orders_pipeline(self, project_runner):
        project_runner.run_scenarios(SCENARIOS)
