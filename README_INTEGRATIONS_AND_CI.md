# Integrations And CI

`fabric-pytester` centers the shared Fabric and OneLake workflow, then lets each test project add destinations for the surrounding systems it needs.

The `examples/` directory includes destination examples for:

- Dataverse: `examples/dataverse_destination.py`
- SQL: `examples/sql_destination.py`
- REST APIs: `examples/rest_destination.py`

Use these examples as starting points for project-specific destinations, or replace them with your own implementations. The `examples/example_project/` directory shows a small consuming-project layout with config, fixtures, a Dataverse destination, scenario JSON, and a pytest test file that can be copied and shaped for a real project.

Use `@pytest.mark.fabric_scenarios` with the same scenario list passed to `run_scenarios(...)`. That keeps each top-level scenario key as its own pytest test while still letting fixtures register Dataverse, REST, SQL, or other project destinations. Each marked test function or method shares setup and pipeline execution across its scenario items; another marked test gets its own separate cycle.

## Dataverse Extension

```python
import pytest

from fabric_pytester.core.http import BearerHttpClient
from tests.destinations.dataverse import DataverseDestination


@pytest.fixture
def dataverse_destination(fabric_token_provider):
    base_url = "https://example.dataverse.test"
    http = BearerHttpClient(
        base_url=base_url,
        token_provider=fabric_token_provider,
        scope=f"{base_url}/.default",
    )
    return DataverseDestination(http=http)


@pytest.fixture
def dataverse_runner(fabric_runner, dataverse_destination):
    return fabric_runner.add_destination("dataverse", dataverse_destination)
```

```json
{
  "orders_with_dataverse": {
    "insert_dataverse_1": {
      "target": "accounts",
      "payload": {
        "name": "Example Ltd"
      },
      "capture": {
        "account_id": "@entity_id"
      }
    },
    "expected_dataverse_1": {
      "target": "accounts",
      "filter": "accountid eq '{account_id}'",
      "expected_count": 1
    },
    "delete_dataverse_1": {
      "target": "accounts",
      "filter": "accountid eq '{account_id}'"
    }
  }
}
```

```python
DATAVERSE_SCENARIOS = ["orders_with_dataverse"]


@pytest.mark.fabric_scenarios(DATAVERSE_SCENARIOS)
def test_orders_with_dataverse(dataverse_runner):
    dataverse_runner.run_scenarios(DATAVERSE_SCENARIOS)
```

## SQL Extension

Use a SQL destination to connect a warehouse, source database, reference database, or any other SQL surface around your Fabric workflow.

```python
import pyodbc
import pytest

from tests.destinations.sql import SqlDestination


@pytest.fixture
def warehouse_destination():
    connection = pyodbc.connect("Driver={ODBC Driver 18 for SQL Server};Server=...")
    return SqlDestination(connection)


@pytest.fixture
def warehouse_runner(fabric_runner, warehouse_destination):
    return fabric_runner.add_destination("warehouse", warehouse_destination)
```

```json
{
  "orders_with_warehouse": {
    "insert_warehouse_1": {
      "target": "orders",
      "sql": "INSERT INTO orders(order_id, status) VALUES ('ORD-1', 'Submitted')"
    },
    "expected_warehouse_1": {
      "target": "orders",
      "sql": "SELECT * FROM orders WHERE order_id = 'ORD-1'",
      "expected_count": 1,
      "fields": {
        "status": "Submitted"
      }
    }
  }
}
```

## Fabric SQL With Existing Tokens

If your test framework already has an `auth_token` fixture, `fabric-pytester` can use it for Fabric API, OneLake, and token-based Fabric SQL connections:

```python
import pytest


@pytest.fixture
def auth_token(existing_auth_client):
    return existing_auth_client.get_access_token()
```

Enable it explicitly:

```toml
[tool.fabric-pytester.environments.dev.fabric]
token_provider = "auth_token"
```

Configure Fabric SQL to use the JDBC bearer-token backend:

```toml
[tool.fabric-pytester.environments.dev.fabric]
workspace_id = "00000000-0000-0000-0000-000000000000"
sql_adapter = "fabric-jdbc-token"
sql_hostname = "example.datawarehouse.fabric.microsoft.com"
sql_database = "example_lakehouse_or_warehouse"
driver_path = "drivers/mssql-jdbc.jar"
```

Install the JDBC optional dependencies:

```bash
pip install "fabric-pytester[jdbc]"
```

The backend uses `jaydebeapi` and passes the token as the Microsoft JDBC `accessToken` property.

## SQL Driver Choices

SQL adapter selection is per backend, not global. Default Fabric SQL uses `mssql-python`; normal Fabric SQL projects only need hostname and database settings:

```toml
[tool.fabric-pytester.environments.dev.fabric]
sql_hostname = "example.datawarehouse.fabric.microsoft.com"
sql_database = "example_lakehouse_or_warehouse"
```

Optional SQL adapters are:

| Adapter | Typical use |
| --- | --- |
| `mssql-python` | Default Fabric SQL backend. |
| `fabric-jdbc-token` | Fabric SQL through the Microsoft JDBC driver using a bearer token. |
| `jdbc` | Any JDBC source/reference database supported by a project driver jar. |
| `pyodbc` | ODBC SQL sources, reference databases, or Fabric SQL when a project prefers ODBC. |

Each backend selects its own driver with `sql_adapter = "..."`, so drivers can co-exist in the same project:

```toml
[tool.fabric-pytester.environments.dev.fabric]
sql_adapter = "mssql-python"
sql_hostname = "example.datawarehouse.fabric.microsoft.com"
sql_database = "example_lakehouse_or_warehouse"
sql_connection_timeout_seconds = 30
sql_query_timeout_seconds = 120

[tool.fabric-pytester.environments.dev.sql_backends.source_jdbc]
sql_adapter = "jdbc"
jdbc_url = "jdbc:vendor://example-host:1234/example_database"
driver_class = "com.vendor.jdbc.Driver"
driver_path = "libs/jdbc/vendor-driver.jar"

[tool.fabric-pytester.environments.dev.sql_backends.reference_pyodbc]
sql_adapter = "pyodbc"
sql_hostname = "reference-sql.example.test"
sql_database = "reference_database"
client_id_secret = "REFERENCE_CLIENT_ID"
client_secret_secret = "REFERENCE_CLIENT_SECRET"
sql_connection_timeout_seconds = 30
sql_query_timeout_seconds = 120
```

Installing or using one adapter does not prevent another adapter being used elsewhere. For example, Fabric SQL can use `mssql-python` while a source-system named backend uses JDBC, and another suite can opt into `fabric-jdbc-token`.

`sql_connection_timeout_seconds` controls connection establishment where the selected adapter supports it. `sql_query_timeout_seconds` is applied to DB-API cursors where supported, including `mssql-python` and `pyodbc`.

The active adapter choice is logged when a backend is created. If a JDBC adapter uses `driver_path`, relative paths are resolved from the pytest project root. In debug mode the resolved `.jar` path and existence check are printed; if the configured driver file is missing, setup fails with a clear configuration error before the connection is attempted.

For a complete standalone `fabric-pytester.toml` with dev/QA environments and multiple SQL backends, see `examples/example_project/fabric-pytester.complete.example.toml`. This can live beside your Python `pyproject.toml`; it does not replace package metadata or general pytest configuration.

Named SQL backends are registered as scenario destinations by name, so a single scenario can seed through one SQL driver and assert Fabric SQL through another:

```json
{
  "orders_from_source_to_fabric": {
    "insert_source_jdbc_1": {
      "sql": "INSERT INTO source_orders(order_id) VALUES ('ORD-1')"
    },
    "run_fabric_1": {
      "item": "pl_process_orders"
    },
    "expected_fabric_1": {
      "sql": "SELECT * FROM analytics.orders WHERE order_id = 'ORD-1'",
      "expected_count": 1
    }
  }
}
```

Here `insert_source_jdbc_1` uses `[tool.fabric-pytester.environments.<env>.sql_backends.source_jdbc]`, while `expected_fabric_1` uses the default Fabric SQL backend.

## REST Extension

```python
import pytest

from tests.destinations.rest import RestDestination


@pytest.fixture
def api_runner(fabric_runner):
    api = RestDestination("https://api.example.test", headers={"x-test": "true"})
    return fabric_runner.add_destination("api", api)
```

```json
{
  "orders_with_api": {
    "insert_api_1": {
      "target": "/orders",
      "json": {
        "orderId": "ORD-{random_numbers_9}"
      }
    },
    "expected_api_1": {
      "target": "/orders",
      "status": 200,
      "contains": "ORD-"
    }
  }
}
```

## CLI Options

These pytest options are useful for local runs, CI overrides, and framework-level wrappers:

| Option | Purpose |
| --- | --- |
| `--fabric-env <name>` | Selects the configured environment. Overrides any mapped project environment option, `FABRIC_ENV`, and `dev`. |
| `--fabric-config <path>` | Loads a specific standalone `fabric-pytester.toml` or compatible TOML config file. |
| `--fabric-scenarios <path>` | Adds or replaces scenario JSON files/directories for this run. Can be supplied more than once. |
| `--fabric-keep-artifacts` | Keeps tracked artifacts and uploaded inputs instead of deleting them after the run. |
| `--fabric-timeout-job <seconds>` | Overrides the Fabric job timeout. |
| `--fabric-timeout-sql <seconds>` | Overrides the Fabric SQL polling timeout. |
| `--fabric-poll-interval <seconds>` | Overrides the polling interval used by Fabric jobs and SQL assertions. |
| `--fabric-auth-provider local\|env\|keyvault` | Selects the secret provider for this run. |
| `--fabric-job-api-style path\|query` | Selects the Fabric item job API URL style for this run. |
| `--fabric-debug` | Prints scenario, SQL, OneLake, Fabric job, and assertion diagnostics. |
| `--fabric-sql-diagnostics` | Runs `SELECT DB_NAME()` after SQL backend creation and prints adapter/database diagnostics. |

## CI

When tests use Fabric APIs, Fabric SQL, or OneLake, provide either the standard Fabric service principal values:

```bash
FABRIC_TENANT_ID=...
FABRIC_CLIENT_ID=...
FABRIC_CLIENT_SECRET=...
```

or explicitly configure `token_provider = "auth_token"` and provide an `auth_token` fixture from your existing test framework.

Run tests with an explicit environment and scenario path when useful:

```bash
pytest --fabric-env=ci --fabric-scenarios=tests/fabric/scenarios
```

Example GitHub Actions step:

```yaml
- name: Run Fabric integration tests
  env:
    FABRIC_TENANT_ID: ${{ secrets.FABRIC_TENANT_ID }}
    FABRIC_CLIENT_ID: ${{ secrets.FABRIC_CLIENT_ID }}
    FABRIC_CLIENT_SECRET: ${{ secrets.FABRIC_CLIENT_SECRET }}
  run: |
    pip install fabric-pytester
    pytest --fabric-env=ci
```

Destination-specific secrets can stay with the consuming project and be consumed by the fixture that creates that destination. The runner receives the registered destination object and uses it from scenario steps.
