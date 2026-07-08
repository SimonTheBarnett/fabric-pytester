import json
import logging

import pytest

from fabric_pytester.core.destinations import SqlBackendDestination, parse_destination_step
from fabric_pytester.core.errors import AssertionGroupError, ScenarioError
from fabric_pytester.core.runner import (
    ScenarioBatch,
    ScenarioGroupContext,
    ScenarioGroupState,
    ScenarioRunner,
)
from fabric_pytester.core.scenario_loader import ScenarioLoader
from fabric_pytester.core.sql_backend import Row, SqlBackend, SqlBackendInfo


class FakeSql(SqlBackend):
    def __init__(self):
        self.executed = []
        self.rows = [Row({"Status": "Submitted"})]
        self.queries = []
        self.closed = False
        self.info = SqlBackendInfo(
            adapter="mssql-python",
            host="fabric-sql.example.test",
            database="warehouse",
        )

    def execute(self, sql, params=()):
        self.executed.append(sql)

    def fetch_one(self, sql, params=()):
        return self.rows[0] if self.rows else None

    def fetch_all(self, sql, params=()):
        self.queries.append(sql)
        return self.rows

    def close(self):
        self.closed = True


class FakeOneLake:
    def __init__(self):
        self.uploaded = []
        self.deleted = []
        self.workspace = "workspace"
        self.lakehouse_root = "root"

    def upload(self, **kwargs):
        self.uploaded.append(kwargs)

        class PathObj:
            file_system = "workspace"
            path = f"root/{kwargs['folder']}/{kwargs['filename']}"

        class Uploaded:
            path = PathObj()
            size = 1

        return Uploaded()

    def delete(self, path):
        self.deleted.append(path.path)

    def download_latest(self, folder, pattern=None):
        return b"done"


class FakeRun:
    def wait(self):
        return {"status": "Completed"}


class FailingRun:
    def wait(self):
        raise RuntimeError("pipeline failed: bad payload")


class FakeFabric:
    def __init__(self):
        self.jobs = []

    def run_item_job(self, **kwargs):
        self.jobs.append(kwargs)
        return FakeRun()


class FailingFabric(FakeFabric):
    def run_item_job(self, **kwargs):
        self.jobs.append(kwargs)
        return FailingRun()


class FakeDestination:
    def __init__(self):
        self.inserted = []
        self.expected_calls = []
        self.deleted = []
        self.rows = [
            {
                "name": "Test Account",
                "accountnumber": "ACC-123",
                "accountid": "account-1",
            }
        ]

    def insert(self, target, payload):
        self.inserted.append((target, payload))
        return {"@entity_id": "account-1", **payload}

    def expected(self, target, **kwargs):
        self.expected_calls.append((target, kwargs.get("filter")))
        rows = [] if kwargs.get("filter") == "name eq 'Missing'" else self.rows
        expected_count = kwargs.get("expected_count")
        if expected_count is not None and len(rows) != expected_count:
            raise AssertionError(f"expected {expected_count} row(s), got {len(rows)}")
        for field, expected in kwargs.get("fields", {}).items():
            if rows[0][field] != expected:
                raise AssertionError(f"{field}: expected {expected!r}, got {rows[0][field]!r}")

    def delete(self, target, **kwargs):
        self.deleted.append((target, kwargs))


class RecordingDestination:
    def __init__(self):
        self.events = []

    def insert(self, target, payload):
        self.events.append(("insert", target, payload))
        return {"id": "order-1"}

    def expected(self, target, **kwargs):
        self.events.append(("expected", target, kwargs))

    def delete(self, target, **kwargs):
        self.events.append(("delete", target, kwargs))
        return {"deleted": 1}

    def run(self, target, **kwargs):
        result = {"target": target, "kwargs": kwargs}
        self.events.append(("run", target, kwargs))
        return result


class FailingDestination:
    def insert(self, target, payload):
        return None

    def expected(self, target, **kwargs):
        raise AssertionError(f"{target} failed")

    def delete(self, target, **kwargs):
        return None


class AllSeededDestination(RecordingDestination):
    def __init__(self, expected_seed_count):
        super().__init__()
        self.expected_seed_count = expected_seed_count

    def expected(self, target, **kwargs):
        insert_count = len([event for event in self.events if event[0] == "insert"])
        if insert_count != self.expected_seed_count:
            raise AssertionError("assertions started before all scenarios were seeded")
        super().expected(target, **kwargs)


class PipelineAwareDestination(RecordingDestination):
    def __init__(self, fabric, expected_job_count):
        super().__init__()
        self.fabric = fabric
        self.expected_job_count = expected_job_count

    def expected(self, target, **kwargs):
        if len(self.fabric.jobs) != self.expected_job_count:
            raise AssertionError("assertions started before all pipelines completed")
        super().expected(target, **kwargs)


class CleanupThenFailDestination:
    def __init__(self):
        self.cleaned = []

    def insert(self, target, payload):
        if target == "second":
            raise RuntimeError("second insert failed")
        return {"_cleanup": (target, lambda: self.cleaned.append(target))}

    def expected(self, target, **kwargs):
        return None

    def delete(self, target, **kwargs):
        return None


class CleanupFailsAfterRunFailsDestination:
    def __init__(self):
        self.cleaned = []
        self.run_count = 0
        self.expected_calls = []

    def insert(self, target, payload):
        return {"_cleanup": (target, self._cleanup)}

    def _cleanup(self):
        self.cleaned.append("cleanup")
        raise RuntimeError("cleanup failed")

    def expected(self, target, **kwargs):
        self.expected_calls.append(target)

    def delete(self, target, **kwargs):
        return None

    def run(self, target, **kwargs):
        self.run_count += 1
        raise RuntimeError("pipeline failed")


def group_context(state, group_key="test_file.py::test_group", expected_count=2):
    state.run_for(group_key, expected_count)
    return ScenarioGroupContext(
        state=state,
        group_key=group_key,
        expected_count=expected_count,
    )


class NonMappingCaptureDestination:
    def insert(self, target, payload):
        return "created"

    def expected(self, target, **kwargs):
        return None

    def delete(self, target, **kwargs):
        return None


def test_scenario_loader_detects_duplicates(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps({"same": {}}), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps({"same": {}}), encoding="utf-8")
    with pytest.raises(ScenarioError, match="Duplicate"):
        ScenarioLoader([tmp_path]).load()


def test_destination_step_parser_supports_underscored_names():
    step = parse_destination_step("expected_reference_api_12")
    assert step is not None
    assert step.operation == "expected"
    assert step.destination == "reference_api"
    assert step.order == 12


def test_destination_step_parser_supports_absent_operation():
    step = parse_destination_step("absent_fabric_1")
    assert step is not None
    assert step.operation == "absent"
    assert step.destination == "fabric"


def test_runner_dispatches_registered_destination_operations(tmp_path):
    scenario_file = tmp_path / "scenario.json"
    scenario_file.write_text(
        json.dumps(
            {
                "orders": {
                    "insert_api_1": {
                        "target": "/orders",
                        "json": {"status": "Submitted"},
                        "capture": {"order_id": "id"},
                    },
                    "delete_api_2": {
                        "target": "/orders/{order_id}",
                        "reason": "reset",
                    },
                    "run_api_1": {
                        "target": "/orders/{order_id}/replay",
                        "mode": "full",
                    },
                    "expected_api_1": {
                        "target": "/orders/{order_id}",
                        "status": 200,
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    api = RecordingDestination()
    runner = ScenarioRunner(ScenarioLoader([scenario_file])).add_destination("api", api)
    batch = runner.run_scenarios(["orders"])
    assert api.events == [
        ("insert", "/orders", {"json": {"status": "Submitted"}}),
        ("delete", "/orders/order-1", {"reason": "reset"}),
        ("run", "/orders/order-1/replay", {"mode": "full"}),
        ("expected", "/orders/order-1", {"status": 200}),
    ]
    assert batch.job_runs == [{"target": "/orders/order-1/replay", "kwargs": {"mode": "full"}}]


def test_runner_filters_explicit_keys_to_current_pytest_scenario(tmp_path):
    scenario_file = tmp_path / "scenario.json"
    scenario_file.write_text(
        json.dumps(
            {
                "orders": {
                    "insert_api_1": {
                        "target": "/orders",
                        "payload": {"status": "Submitted"},
                    }
                },
                "returns": {
                    "insert_api_1": {
                        "target": "/returns",
                        "payload": {"status": "Submitted"},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    api = RecordingDestination()
    runner = ScenarioRunner(
        ScenarioLoader([scenario_file]), current_scenario_key="returns"
    ).add_destination("api", api)

    batch = runner.run_scenarios(["orders", "returns"])

    assert [execution.context.scenario_key for execution in batch.executions] == ["returns"]
    assert api.events == [("insert", "/returns", {"status": "Submitted"})]


def test_runner_reports_marker_and_explicit_key_mismatch(tmp_path):
    scenario_file = tmp_path / "scenario.json"
    scenario_file.write_text(json.dumps({"orders": {}, "returns": {}}), encoding="utf-8")
    runner = ScenarioRunner(ScenarioLoader([scenario_file]), current_scenario_key="returns")

    with pytest.raises(ScenarioError, match="Current pytest scenario 'returns'"):
        runner.run_scenarios(["orders"])


def test_runner_rejects_multiple_keys_without_pytest_scenario_split(tmp_path):
    scenario_file = tmp_path / "scenario.json"
    scenario_file.write_text(json.dumps({"orders": {}, "returns": {}}), encoding="utf-8")
    runner = ScenarioRunner(ScenarioLoader([scenario_file]))

    with pytest.raises(ScenarioError, match="one test per scenario"):
        runner.run_scenarios(["orders", "returns"])


def test_runner_cleans_partial_data_setup_when_later_insert_fails(tmp_path):
    scenario_file = tmp_path / "scenario.json"
    scenario_file.write_text(
        json.dumps(
            {
                "orders": {
                    "insert_api_1": {"target": "first", "payload": {"id": "first"}},
                    "insert_api_2": {"target": "second", "payload": {"id": "second"}},
                }
            }
        ),
        encoding="utf-8",
    )
    api = CleanupThenFailDestination()
    runner = ScenarioRunner(ScenarioLoader([scenario_file])).add_destination("api", api)

    with pytest.raises(RuntimeError, match="second insert failed"):
        runner.run_scenarios(["orders"])

    assert api.cleaned == ["first"]


def test_scenario_group_cleanup_attempts_every_prepared_group(tmp_path):
    state = ScenarioGroupState()
    calls = []
    first_batch = ScenarioBatch(executions=[])
    second_batch = ScenarioBatch(executions=[])
    first_batch.cleanup.add("first", lambda: (_ for _ in ()).throw(RuntimeError("first failed")))
    second_batch.cleanup.add("second", lambda: calls.append("second"))
    first_run = state.run_for("test_file.py::test_first", 1)
    first_run.runner = ScenarioRunner()
    first_run.batch = first_batch
    second_run = state.run_for("test_file.py::test_second", 1)
    second_run.runner = ScenarioRunner()
    second_run.batch = second_batch

    with pytest.raises(ScenarioError, match="Scenario group cleanup failed"):
        state.cleanup()

    assert calls == ["second"]
    assert first_run.batch is None
    assert second_run.batch is None


def test_grouped_runner_seeds_all_scenarios_and_runs_pipelines_before_assertions(tmp_path):
    scenario_file = tmp_path / "scenario.json"
    scenario_file.write_text(
        json.dumps(
            {
                "orders": {
                    "insert_api_1": {"target": "/seed/orders", "payload": {"id": "orders"}},
                    "run_fabric_1": {"item": "pl_process_orders", "job_type": "Pipeline"},
                    "expected_api_1": {"target": "/assert/orders"},
                },
                "returns": {
                    "insert_api_1": {"target": "/seed/returns", "payload": {"id": "returns"}},
                    "run_fabric_1": {"item": "pl_process_orders", "job_type": "Pipeline"},
                    "expected_api_1": {"target": "/assert/returns"},
                },
            }
        ),
        encoding="utf-8",
    )
    state = ScenarioGroupState()
    api = AllSeededDestination(expected_seed_count=2)
    fabric = FakeFabric()
    keys = ["orders", "returns"]

    first_runner = ScenarioRunner(
        ScenarioLoader([scenario_file]),
        fabric_client=fabric,
        current_scenario_key="orders",
        scenario_group_context=group_context(state),
    ).add_destination("api", api)
    first_batch = first_runner.run_scenarios(keys)

    second_runner = ScenarioRunner(
        ScenarioLoader([scenario_file]),
        fabric_client=fabric,
        current_scenario_key="returns",
        scenario_group_context=group_context(state),
    ).add_destination("api", api)
    second_batch = second_runner.run_scenarios(keys)

    state.cleanup()

    assert [execution.context.scenario_key for execution in first_batch.executions] == ["orders"]
    assert [execution.context.scenario_key for execution in second_batch.executions] == ["returns"]
    assert len([event for event in api.events if event[0] == "insert"]) == 2
    assert len([event for event in api.events if event[0] == "expected"]) == 2
    assert len(fabric.jobs) == 1


def test_grouped_runner_runs_all_distinct_pipelines_before_first_assertion(tmp_path):
    scenario_file = tmp_path / "scenario.json"
    scenario_file.write_text(
        json.dumps(
            {
                "orders": {
                    "insert_api_1": {"target": "/seed/orders", "payload": {"id": "orders"}},
                    "run_fabric_1": {"item": "pl_process_orders", "job_type": "Pipeline"},
                    "expected_api_1": {"target": "/assert/orders"},
                },
                "returns": {
                    "insert_api_1": {"target": "/seed/returns", "payload": {"id": "returns"}},
                    "run_fabric_1": {"item": "pl_process_returns", "job_type": "Pipeline"},
                    "expected_api_1": {"target": "/assert/returns"},
                },
            }
        ),
        encoding="utf-8",
    )
    state = ScenarioGroupState()
    fabric = FakeFabric()
    api = PipelineAwareDestination(fabric=fabric, expected_job_count=2)
    keys = ["orders", "returns"]

    first_runner = ScenarioRunner(
        ScenarioLoader([scenario_file]),
        fabric_client=fabric,
        current_scenario_key="orders",
        scenario_group_context=group_context(state),
    ).add_destination("api", api)
    first_runner.run_scenarios(keys)

    second_runner = ScenarioRunner(
        ScenarioLoader([scenario_file]),
        fabric_client=fabric,
        current_scenario_key="returns",
        scenario_group_context=group_context(state),
    ).add_destination("api", api)
    second_runner.run_scenarios(keys)

    state.cleanup()

    assert [job["item_name"] for job in fabric.jobs] == [
        "pl_process_orders",
        "pl_process_returns",
    ]
    assert len([event for event in api.events if event[0] == "expected"]) == 2


def test_grouped_runner_caches_pipeline_failure_and_skips_later_assertions(tmp_path):
    scenario_file = tmp_path / "scenario.json"
    scenario_file.write_text(
        json.dumps(
            {
                "orders": {
                    "insert_api_1": {"target": "/seed/orders", "payload": {"id": "orders"}},
                    "run_fabric_1": {"item": "pl_process_orders", "job_type": "Pipeline"},
                    "expected_api_1": {"target": "/assert/orders"},
                },
                "returns": {
                    "insert_api_1": {"target": "/seed/returns", "payload": {"id": "returns"}},
                    "run_fabric_1": {"item": "pl_process_orders", "job_type": "Pipeline"},
                    "expected_api_1": {"target": "/assert/returns"},
                },
            }
        ),
        encoding="utf-8",
    )
    state = ScenarioGroupState()
    api = RecordingDestination()
    fabric = FailingFabric()
    keys = ["orders", "returns"]

    first_runner = ScenarioRunner(
        ScenarioLoader([scenario_file]),
        fabric_client=fabric,
        current_scenario_key="orders",
        scenario_group_context=group_context(state),
    ).add_destination("api", api)
    with pytest.raises(RuntimeError, match="pipeline failed"):
        first_runner.run_scenarios(keys)

    second_runner = ScenarioRunner(
        ScenarioLoader([scenario_file]),
        fabric_client=fabric,
        current_scenario_key="returns",
        scenario_group_context=group_context(state),
    ).add_destination("api", api)
    with pytest.raises(ScenarioError, match="setup or pipeline phase failed"):
        second_runner.run_scenarios(keys)

    assert len(fabric.jobs) == 1
    assert not [event for event in api.events if event[0] == "expected"]


def test_grouped_runner_preserves_pipeline_failure_when_cleanup_also_fails(tmp_path, caplog):
    scenario_file = tmp_path / "scenario.json"
    scenario_file.write_text(
        json.dumps(
            {
                "orders": {
                    "insert_api_1": {"target": "/seed/orders", "payload": {"id": "orders"}},
                    "run_api_1": {"target": "/pipeline"},
                    "expected_api_1": {"target": "/assert/orders"},
                },
                "returns": {
                    "insert_api_1": {"target": "/seed/returns", "payload": {"id": "returns"}},
                    "run_api_1": {"target": "/pipeline"},
                    "expected_api_1": {"target": "/assert/returns"},
                },
            }
        ),
        encoding="utf-8",
    )
    state = ScenarioGroupState()
    api = CleanupFailsAfterRunFailsDestination()
    keys = ["orders", "returns"]

    first_runner = ScenarioRunner(
        ScenarioLoader([scenario_file]),
        current_scenario_key="orders",
        scenario_group_context=group_context(state),
    ).add_destination("api", api)
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match="pipeline failed"):
        first_runner.run_scenarios(keys)

    second_runner = ScenarioRunner(
        ScenarioLoader([scenario_file]),
        current_scenario_key="returns",
        scenario_group_context=group_context(state),
    ).add_destination("api", api)
    with pytest.raises(ScenarioError, match="pipeline failed"):
        second_runner.run_scenarios(keys)

    assert api.cleaned == ["cleanup", "cleanup"]
    assert api.run_count == 1
    assert api.expected_calls == []
    assert any(
        "Cleanup failed after scenario group setup failure" in record.message
        for record in caplog.records
    )


def test_runner_reports_unknown_registered_destination(tmp_path):
    scenario_file = tmp_path / "scenario.json"
    scenario_file.write_text(
        json.dumps({"orders": {"insert_api_1": {"target": "/orders", "json": {}}}}),
        encoding="utf-8",
    )
    runner = ScenarioRunner(ScenarioLoader([scenario_file]))
    with pytest.raises(ScenarioError, match="Destination 'api' is not configured"):
        runner.run_scenarios(["orders"])


def test_runner_groups_registered_destination_assertion_failures(tmp_path):
    scenario_file = tmp_path / "scenario.json"
    scenario_file.write_text(
        json.dumps(
            {
                "orders": {
                    "expected_api_1": {"target": "/orders/1"},
                    "expected_api_2": {"target": "/orders/2"},
                }
            }
        ),
        encoding="utf-8",
    )
    runner = ScenarioRunner(ScenarioLoader([scenario_file])).add_destination(
        "api", FailingDestination()
    )
    with pytest.raises(AssertionGroupError) as exc_info:
        runner.run_scenarios(["orders"])
    message = str(exc_info.value)
    assert "expected_api_1" in message
    assert "expected_api_2" in message


def test_runner_rejects_capture_from_non_mapping_destination_result(tmp_path):
    scenario_file = tmp_path / "scenario.json"
    scenario_file.write_text(
        json.dumps(
            {
                "orders": {
                    "insert_api_1": {
                        "target": "/orders",
                        "payload": {"status": "Submitted"},
                        "capture": {"order_id": "id"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    runner = ScenarioRunner(ScenarioLoader([scenario_file])).add_destination(
        "api", NonMappingCaptureDestination()
    )
    with pytest.raises(ScenarioError, match="capture requires a mapping result"):
        runner.run_scenarios(["orders"])


def test_runner_runs_data_pipeline_assertions_and_cleanup(tmp_path):
    scenario_file = tmp_path / "scenario.json"
    scenario_file.write_text(
        json.dumps(
            {
                "orders": {
                    "variables": {"order_status": "Submitted"},
                    "insert_fabric_1": {"target": "sql", "sql": "insert {random_numbers_9}"},
                    "insert_onelake_1": {
                        "folder": "incoming_orders",
                        "filename": "file_{random_numbers_9}.json",
                        "records": [{"Status": "{order_status}"}],
                    },
                    "insert_dataverse_1": {
                        "target": "accounts",
                        "payload": {
                            "name": "Test Account",
                            "accountnumber": "ACC-{random_numbers_3}",
                        },
                        "capture": {"account_id": "@entity_id"},
                    },
                    "run_fabric_1": {"item": "pipeline", "job_type": "Pipeline"},
                    "expected_fabric_1": {
                        "target": "sql",
                        "sql": "select 1",
                        "expected_count": 1,
                        "fields": {"Status": "{order_status}"},
                    },
                    "expected_dataverse_1": {
                        "target": "accounts",
                        "filter": "accountid eq '{account_id}'",
                        "expected_count": 1,
                        "fields": {"name": "Test Account"},
                    },
                    "expected_dataverse_2": {
                        "target": "accounts",
                        "filter": "name eq 'Missing'",
                        "expected_count": 0,
                    },
                    "expected_onelake_1": {"folder": "out", "contains": "done"},
                }
            }
        ),
        encoding="utf-8",
    )
    sql = FakeSql()
    onelake = FakeOneLake()
    fabric = FakeFabric()
    dataverse = FakeDestination()
    runner = ScenarioRunner(
        ScenarioLoader([scenario_file]),
        fabric_client=fabric,
        onelake_client=onelake,
        fabric_sql=sql,
        artifact_dir=tmp_path / "artifacts",
    ).add_destination("dataverse", dataverse)
    scenario_batch = runner.run_scenarios(["orders"])
    assert sql.executed
    assert fabric.jobs[0]["item_name"] == "pipeline"
    assert onelake.uploaded
    assert onelake.deleted
    assert dataverse.inserted[0][0] == "accounts"
    assert dataverse.expected_calls[0] == ("accounts", "accountid eq 'account-1'")
    assert (tmp_path / "artifacts" / "orders.context.json").exists()
    assert scenario_batch.job_runs


def test_runner_can_use_fabric_sql_and_registered_sql_backend_in_same_scenario(tmp_path):
    scenario_file = tmp_path / "scenario.json"
    scenario_file.write_text(
        json.dumps(
            {
                "orders": {
                    "insert_source_1": {
                        "sql": "insert into source_orders values ('ORD-1')",
                    },
                    "expected_fabric_1": {
                        "sql": "select * from analytics.orders where id = 'ORD-1'",
                        "expected_count": 1,
                        "fields": {"Status": "Submitted"},
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    fabric_sql = FakeSql()
    source_sql = FakeSql()
    runner = ScenarioRunner(ScenarioLoader([scenario_file]), fabric_sql=fabric_sql)
    runner.add_destination("source", SqlBackendDestination(source_sql))
    runner.run_scenarios(["orders"])
    assert source_sql.executed == ["insert into source_orders values ('ORD-1')"]
    assert fabric_sql.queries == ["select * from analytics.orders where id = 'ORD-1'"]


def test_runner_deduplicates_fabric_run_steps(tmp_path):
    scenario_file = tmp_path / "scenario.json"
    scenario_file.write_text(
        json.dumps(
            {
                "orders": {
                    "run_fabric_1": {
                        "item": "pipeline",
                        "job_type": "Pipeline",
                        "parameters": {"mode": "full"},
                    },
                    "run_fabric_2": {
                        "item": "pipeline",
                        "job_type": "Pipeline",
                        "parameters": {"mode": "full"},
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    fabric = FakeFabric()
    runner = ScenarioRunner(ScenarioLoader([scenario_file]), fabric_client=fabric)
    batch = runner.run_scenarios(["orders"])
    assert len(fabric.jobs) == 1
    assert len(batch.job_runs) == 1


def test_runner_groups_assertion_failures(tmp_path):
    path = tmp_path / "scenario.json"
    path.write_text(
        json.dumps(
            {
                "bad": {
                    "expected_fabric_1": {
                        "target": "sql",
                        "sql": "select",
                        "expected_count": 1,
                        "fields": {"Status": "X"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    runner = ScenarioRunner(ScenarioLoader([path]), fabric_sql=FakeSql())
    with pytest.raises(AssertionGroupError) as exc_info:
        runner.run_scenarios(["bad"])
    message = str(exc_info.value)
    assert "Status" in message
    assert "sql='select'" in message


def test_runner_supports_absent_fabric_sql_assertions(tmp_path):
    path = tmp_path / "scenario.json"
    path.write_text(
        json.dumps(
            {
                "missing": {
                    "absent_fabric_1": {
                        "target": "sql",
                        "sql": "select * from orders where id = 'missing'",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    sql = FakeSql()
    sql.rows = []
    runner = ScenarioRunner(ScenarioLoader([path]), fabric_sql=sql)
    runner.run_scenarios(["missing"])
    assert sql.queries == ["select * from orders where id = 'missing'"]
    assert sql.closed


def test_runner_reports_sql_for_failed_absent_fabric_assertion(tmp_path):
    path = tmp_path / "scenario.json"
    path.write_text(
        json.dumps(
            {
                "present": {
                    "absent_fabric_1": {
                        "target": "sql",
                        "sql": "select * from orders",
                        "timeout_seconds": 0.01,
                        "poll_interval_seconds": 0,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    runner = ScenarioRunner(ScenarioLoader([path]), fabric_sql=FakeSql())
    with pytest.raises(AssertionGroupError) as exc_info:
        runner.run_scenarios(["present"])
    assert "sql='select * from orders'" in str(exc_info.value)
    assert "actual_count=1" in str(exc_info.value)
    assert "sample_rows" in str(exc_info.value)
    assert "adapter='mssql-python'" in str(exc_info.value)
    assert "sql_host='fabric-sql.example.test'" in str(exc_info.value)


def test_runner_logs_phases_assertions_and_sql_polls(tmp_path, caplog):
    path = tmp_path / "scenario.json"
    path.write_text(
        json.dumps(
            {
                "orders": {
                    "insert_fabric_1": {"target": "sql", "sql": "insert into setup values (1)"},
                    "expected_fabric_1": {
                        "target": "sql",
                        "sql": "select * from orders where id = 'ORD-1'",
                        "expected_count": 1,
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    runner = ScenarioRunner(ScenarioLoader([path]), fabric_sql=FakeSql())

    with caplog.at_level(logging.INFO):
        runner.run_scenarios(["orders"])

    messages = [record.getMessage() for record in caplog.records]
    assert "Data setup completed" in messages
    assert "Pipelines completed" in messages
    assert "Fabric pipeline completed successfully. Starting assertions..." in messages
    assert "Assertions completed" in messages
    assert "Cleanup completed" in messages
    assert any(
        "Starting assertion scenario=orders block=expected_fabric_1 "
        "destination=fabric operation=expected" in message
        for message in messages
    )
    assert any(
        "SQL assertion poll scenario=orders block=expected_fabric_1 expected_count=1 "
        "actual_count=1" in message
        for message in messages
    )


def test_runner_debug_prints_scenario_placeholders_and_sql_details(tmp_path, capsys):
    path = tmp_path / "scenario.json"
    path.write_text(
        json.dumps(
            {
                "orders": {
                    "variables": {"order_status": "Submitted"},
                    "expected_fabric_1": {
                        "target": "sql",
                        "sql": "select * from orders where status = '{order_status}'",
                        "expected_count": 1,
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    runner = ScenarioRunner(ScenarioLoader([path]), fabric_sql=FakeSql(), debug=True)

    runner.run_scenarios(["orders"])

    output = capsys.readouterr().out
    assert "Scenario=orders block=expected_fabric_1 destination=fabric operation=expected" in output
    assert "'order_status': 'Submitted'" in output
    assert "SQL assertion scenario=orders block=expected_fabric_1 adapter=mssql-python" in output
    assert "expected_count=1 actual_count=1" in output


def test_runner_uses_configured_sql_polling_defaults_for_fabric_sql(tmp_path):
    path = tmp_path / "scenario.json"
    path.write_text(
        json.dumps(
            {
                "orders": {
                    "expected_fabric_1": {
                        "target": "sql",
                        "sql": "select * from orders where id = 'missing'",
                        "expected_count": 1,
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    sql = FakeSql()
    sql.rows = []
    runner = ScenarioRunner(
        ScenarioLoader([path]),
        fabric_sql=sql,
        fabric_sql_seconds=0.01,
        fabric_poll_interval_seconds=0,
    )

    with pytest.raises(AssertionGroupError) as exc_info:
        runner.run_scenarios(["orders"])

    message = str(exc_info.value)
    assert "select * from orders where id = 'missing'" in message
    assert "expected_count=1" in message
    assert "actual_count=0" in message


def test_expected_key_polls_until_rows_without_expected_count(tmp_path):
    path = tmp_path / "scenario.json"
    path.write_text(
        json.dumps(
            {
                "legacy": {
                    "expected_fabric_1": {
                        "target": "sql",
                        "sql": "select * from orders",
                        "expected_key": "OrderId",
                        "fields": {"Status": "Submitted"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    sql = FakeSql()
    runner = ScenarioRunner(ScenarioLoader([path]), fabric_sql=sql)
    runner.run_scenarios(["legacy"])
    assert sql.queries == ["select * from orders"]


def test_runner_deletes_onelake_by_folder_and_filename(tmp_path):
    path = tmp_path / "scenario.json"
    path.write_text(
        json.dumps(
            {
                "cleanup": {
                    "delete_onelake_1": {
                        "target": "file",
                        "folder": "incoming",
                        "filename": "stale.json",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    onelake = FakeOneLake()
    runner = ScenarioRunner(ScenarioLoader([path]), onelake_client=onelake)
    runner.run_scenarios(["cleanup"])
    assert onelake.deleted == ["root/incoming/stale.json"]


def test_runner_honors_onelake_cleanup_false(tmp_path):
    path = tmp_path / "scenario.json"
    path.write_text(
        json.dumps(
            {
                "upload": {
                    "insert_onelake_1": {
                        "target": "file",
                        "folder": "incoming",
                        "filename": "keep.json",
                        "content": "keep",
                        "cleanup": False,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    onelake = FakeOneLake()
    runner = ScenarioRunner(ScenarioLoader([path]), onelake_client=onelake)
    runner.run_scenarios(["upload"])
    assert onelake.uploaded
    assert onelake.deleted == []
