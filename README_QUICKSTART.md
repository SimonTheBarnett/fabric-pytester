# Quickstart

This quickstart builds one scenario using the built-in Fabric and OneLake destinations.

The scenario will:

1. Upload an order file to OneLake.
2. Run a Fabric pipeline.
3. Assert that Fabric SQL contains the expected output.
4. Assert that a processed OneLake file was produced.

Use this path when you want the shortest runnable setup before adding project-specific destinations.

## Install

```bash
pip install fabric-pytester
```

## Configure

Create `fabric-pytester.toml`:

```toml
[tool.fabric-pytester]
scenario_paths = ["tests/fabric/scenarios"]
artifact_dir = "results/artifacts/fabric"

[tool.fabric-pytester.timeouts]
fabric_job_seconds = 3600
fabric_sql_seconds = 300
fabric_poll_interval_seconds = 10

[tool.fabric-pytester.environments.dev.fabric]
base_url = "https://api.fabric.microsoft.com"
workspace_id = "00000000-0000-0000-0000-000000000000"
sql_hostname = "example.datawarehouse.fabric.microsoft.com"
sql_database = "example_lakehouse_or_warehouse"

[tool.fabric-pytester.environments.dev.onelake]
account_url = "https://onelake.dfs.fabric.microsoft.com"
workspace = "example_workspace_or_workspace_guid"
lakehouse_root = "example_lakehouse.Lakehouse/Files"
```

By default the plugin looks for `fabric-pytester.toml` in the pytest root, then `[tool.fabric-pytester]` in `pyproject.toml`. Use `pytest --fabric-config path/to/fabric-pytester.toml` to point at a different config file.

`fabric-pytester.toml` can sit alongside your normal Python `pyproject.toml`: keep package metadata and general pytest options in `pyproject.toml`, and keep Fabric environments, OneLake, secrets, and SQL backend settings in `fabric-pytester.toml`. See `examples/example_project/fabric-pytester.complete.example.toml` for a fuller standalone example.

Provide credentials through environment variables:

```bash
export FABRIC_CLIENT_ID=...
export FABRIC_CLIENT_SECRET=...
export FABRIC_TENANT_ID=...
```

## Add A Scenario

Create `tests/fabric/scenarios/orders.json`:

```json
{
  "orders_pipeline": {
    "variables": {
      "order_status": "Submitted"
    },
    "insert_onelake_1": {
      "target": "file",
      "folder": "incoming_orders",
      "filename": "Orders_{random_numbers_9}.json",
      "records": [
        {
          "OrderId": "ORD-{random_numbers_9}",
          "Status": "{order_status}"
        }
      ]
    },
    "run_fabric_1": {
      "item": "pl_process_orders",
      "job_type": "Pipeline"
    },
    "expected_fabric_1": {
      "target": "sql",
      "sql": "SELECT TOP 1 * FROM analytics.orders WHERE Status = '{order_status}'",
      "expected_count": 1,
      "fields": {
        "Status": "{order_status}"
      }
    },
    "expected_onelake_1": {
      "target": "file",
      "folder": "processed_orders",
      "pattern": "*.json",
      "contains": "ORD-"
    }
  }
}
```

## Add A Test

Create `tests/test_orders_pipeline.py`:

```python
import pytest


SCENARIOS = ["orders_pipeline"]


@pytest.mark.fabric_scenarios(SCENARIOS)
def test_orders_pipeline(fabric_runner):
    fabric_runner.run_scenarios(SCENARIOS)
```

Each scenario listed in `@pytest.mark.fabric_scenarios` is collected as its own pytest test. With multiple scenario keys, pytest reports them separately as `test_orders_pipeline[scenario_key]`, while `run_scenarios(SCENARIOS)` still shows the explicit scenario set this test covers.

## Run

```bash
pytest --fabric-env=dev
```

If your project already has an environment option, map `fabric-pytester` to it:

```toml
[tool.fabric-pytester]
pytest_env_option = "env"
```

Then `pytest --env=dev` selects the same environment for Fabric tests. `--fabric-env` can still be used when you want a Fabric-specific override.

Add project destinations when a scenario needs another system. Register those destinations in Python and reference them from JSON as `insert_<destination>_1`, `expected_<destination>_1`, or `delete_<destination>_1`.

For a copyable project shape with `pyproject.toml`, `fabric-pytester.toml`, fixtures, Dataverse extension code, multiple scenario files, and multiple scenario test classes, see `examples/example_project/`.

Next, use `README_USAGE_GUIDE.md` for the destination interface and reusable framework patterns, or `README_INTEGRATIONS_AND_CI.md` for Dataverse, SQL, REST, and CI examples.
