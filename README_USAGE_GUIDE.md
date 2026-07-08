# Usage Guide

`fabric-pytester` orchestrates Fabric integration tests. The built-in runner covers Fabric jobs, Fabric SQL checks, OneLake files, scenario loading, variables, retries, and cleanup.

For other systems, add a destination in your test project and register it with the runner. This gives each project a straightforward way to plug in Dataverse or CRM systems, SQL Server, REST APIs, internal services, or any other setup/assertion surface.

## ScenarioRunner

```python
from fabric_pytester import ScenarioRunner
from fabric_pytester.core.scenario_loader import ScenarioLoader

runner = ScenarioRunner(ScenarioLoader(["tests/fabric/scenarios"]))
runner.run_scenarios(["orders_pipeline"])
```

In pytest tests, keep the explicit scenario list in `run_scenarios(...)` and add the same list to `@pytest.mark.fabric_scenarios(...)`. When the list contains more than one scenario, the marker is required so pytest can register one test per scenario.

The pytest fixture `fabric_runner` creates a runner from `fabric-pytester.toml` and auto-registers destinations when their config exists:

- `fabric`: Fabric job execution and Fabric SQL assertions.
- `onelake`: OneLake file upload, file assertions, and tracked cleanup.
- `<sql_backend_name>`: each `environments.<env>.sql_backends.<name>` entry is registered as a SQL destination for `insert`, `expected`, `absent`, and `delete` steps.

## Per-Scenario Pytest Tests

Mark tests with the scenario keys they cover:

```python
import pytest


SCENARIOS = ["orders_pipeline", "returns_pipeline"]


@pytest.mark.fabric_scenarios(SCENARIOS)
def test_pipeline_course_results(fabric_runner):
    fabric_runner.run_scenarios(SCENARIOS)
```

Pytest collects one test per scenario:

```text
test_pipeline_course_results[orders_pipeline]
test_pipeline_course_results[returns_pipeline]
```

Inside a parameterized test, `fabric_runner.run_scenarios(SCENARIOS)` automatically narrows to the current pytest-selected scenario. Each scenario gets its own pytest result, duration, logs, context artifact, and cleanup.

The same rule applies to methods in a class:

```python
SCENARIOS = [
    "scenario_a",
    "scenario_b",
    "scenario_c",
    "scenario_d",
    "scenario_e",
]


class TestPipelineCourseResults:
    @pytest.mark.fabric_scenarios(SCENARIOS)
    def test_pipeline_course_results(self, fabric_runner):
        fabric_runner.run_scenarios(SCENARIOS)
```

That method collects five pytest tests: one for each scenario key in `SCENARIOS`.

One marked test function or method is one scenario group. The first pytest item for that group prepares the whole group before assertions begin: all scenario inserts are executed, then all Fabric pipeline runs in the group are started and waited for. After that, each pytest item runs only the assertions for its current scenario. This usually means the first scenario item in the group does the slow setup and pipeline work, while later items are faster assertion-only checks.

If group setup or a pipeline run fails, every scenario item in that marked test group fails with that setup or pipeline failure reason. Assertions are not run for that group because the shared prerequisite did not complete. A different marked function or method, even in another test file, gets its own separate setup, pipeline, assertion, and cleanup cycle.

You can also let the marker discover scenario keys from configured `scenario_paths` with `@pytest.mark.fabric_scenarios()`, but the explicit list form is the clearest option for normal test code because the test body still states what it is allowed to run.

## Destination Interface

A destination is any object with these methods:

```python
class Destination:
    def insert(self, target, payload):
        ...

    def expected(self, target, **kwargs):
        ...

    def delete(self, target, **kwargs):
        ...
```

`insert` may return a mapping. Scenario `capture` entries copy values from that mapping into later placeholders.

`expected` should raise `AssertionError` or another exception when the expected state is not present. The runner groups assertion failures across scenarios.

Destinations may also provide a `run` method when a scenario needs to trigger an action:

```python
class RunnableDestination:
    def run(self, target, **kwargs):
        ...
```

Scenario steps call that method with `run_<destination>_<order>`.

## Register Destinations

Register destinations in a project fixture:

```python
import pytest


@pytest.fixture
def dataverse_runner(fabric_runner, dataverse_destination):
    return fabric_runner.add_destination("dataverse", dataverse_destination)
```

Then reference the registered name in JSON:

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
    }
  }
}
```

## Framework Integration Pattern

If you are bringing `fabric-pytester` into a project test framework, register shared destinations once in your framework `conftest.py` or fixture package:

```python
import pytest


@pytest.fixture
def fabric_project_runner(fabric_runner, dataverse_destination, reference_api_destination):
    return (
        fabric_runner
        .add_destination("dataverse", dataverse_destination)
        .add_destination("reference_api", reference_api_destination)
    )
```

Framework tests can depend on `fabric_project_runner`, while scenario authors use stable destination names in JSON:

```json
{
  "orders_project_flow": {
    "insert_dataverse_1": {
      "target": "accounts",
      "payload": { "name": "Example Ltd" },
      "capture": { "account_id": "@entity_id" }
    },
    "expected_reference_api_1": {
      "target": "/orders",
      "status": 200,
      "contains": "{account_id}"
    }
  }
}
```

You can also provide `fabric_placeholder_providers` from your framework to add project-specific values without changing scenario files.

## Step Naming

Destination steps use:

```text
<operation>_<destination>_<order>
```

Supported operations are:

- `insert`: setup or seed data through a destination.
- `expected`: assert destination state.
- `absent`: assert matching destination state is absent.
- `delete`: remove destination state.
- `run`: execute a destination action. Built-in `run_fabric_1` executes a Fabric item job.

Examples:

- `insert_onelake_1`
- `run_fabric_1`
- `expected_fabric_1`
- `absent_fabric_1`
- `expected_onelake_1`
- `insert_dataverse_1`
- `delete_reference_1`

## Built-In Fabric Steps

Run a Fabric item:

```json
"run_fabric_1": {
  "item": "pl_process_orders",
  "job_type": "Pipeline",
  "parameters": {
    "validate_only": false
  }
}
```

Assert Fabric SQL:

```json
"expected_fabric_1": {
  "target": "sql",
  "sql": "SELECT TOP 1 * FROM analytics.orders WHERE OrderId = 'ORD-1'",
  "expected_count": 1,
  "fields": {
    "Status": "Submitted"
  }
}
```

If `expected_count` is omitted, Fabric SQL polling waits until at least one row is returned. This preserves the old `expected_key` style where the presence of a row is enough before checking fields:

```json
"expected_fabric_1": {
  "target": "sql",
  "sql": "SELECT TOP 1 * FROM analytics.orders WHERE OrderId = 'ORD-1'",
  "expected_key": "OrderId",
  "fields": {
    "Status": "Submitted"
  }
}
```

Assert Fabric SQL rows are absent:

```json
"absent_fabric_1": {
  "target": "sql",
  "sql": "SELECT * FROM analytics.orders WHERE OrderId = 'ORD-should-not-exist'"
}
```

Execute Fabric SQL as setup or cleanup:

```json
"insert_fabric_1": {
  "target": "sql",
  "sql": "INSERT INTO test_control VALUES ('ORD-1')"
}
```

Field assertions support exact values, legacy matcher tokens, and matcher objects:

```json
"fields": {
  "DeletedAt": "{NULL}",
  "CreatedAt": "{DATE}",
  "CorrelationId": "{UUID}",
  "Payload": { "contains": "Submitted", "count": 2 },
  "Reference": { "regex": "^ORD-[0-9]+$" }
}
```

Supported matcher tokens are `{NULL}`, `{!NULL}`, `{DATE}`, and `{UUID}`. Matcher objects support `equals`, `contains`, `count`, and `regex`.

Fabric SQL assertions use the configured SQL timeout and poll interval by default:

```toml
[tool.fabric-pytester.timeouts]
fabric_sql_seconds = 300
fabric_poll_interval_seconds = 10
```

Each SQL assertion poll is logged with the scenario key, assertion block key, expected count, actual count, elapsed time, and a shortened rendered SQL statement. SQL assertion failures include the active adapter, SQL host/database, rendered SQL, expected row count, final actual row count, elapsed time, rendered placeholders, and sample rows when any were returned.

## Fabric Job Behavior

Fabric jobs default to a 3600 second timeout and a 10 second polling interval. Every poll is logged with the Fabric item name or id, job instance id, current status, and elapsed time.

If Fabric reports a terminal failure state such as `Failed`, `Cancelled`, or `Canceled`, the test fails immediately and includes the failure details returned by the Fabric API. If polling times out, the failure includes the last known status and the last Fabric response payload.

The runner also logs major phase completion: data setup, pipelines, assertions, and cleanup. After the pipeline phase finishes successfully it logs that pipeline execution completed and assertions are starting.

## Built-In OneLake Steps

Upload a file:

```json
"insert_onelake_1": {
  "target": "file",
  "folder": "incoming_orders",
  "filename": "Orders_{random_numbers_9}.json",
  "records": [
    { "OrderId": "ORD-{random_numbers_9}" }
  ],
  "capture": "input_path"
}
```

The upload is automatically deleted after the scenario unless `cleanup` is `false`.

Assert a file:

```json
"expected_onelake_1": {
  "target": "file",
  "folder": "processed_orders",
  "pattern": "*.json",
  "contains": "ORD-"
}
```

Delete a file by path, or by folder and filename:

```json
"delete_onelake_1": {
  "target": "file",
  "folder": "incoming_orders",
  "filename": "stale.json"
}
```

## Variables And Placeholders

Scenario variables and placeholders are rendered before each step executes. Placeholders use `{name}` syntax inside strings and are rendered recursively in dictionaries and lists.

```json
{
  "variables": {
    "order_status": "Submitted"
  },
  "insert_onelake_1": {
    "filename": "Orders_{random_numbers_9}.json",
    "records": [{ "Status": "{order_status}", "Date": "{current_date}" }]
  }
}
```

Unknown placeholders are left unchanged, which helps catch typos without silently inventing values.

Built-in placeholders:

| Placeholder | Example value | Notes |
| --- | --- | --- |
| `{scenario_key}` | `orders_pipeline` | Scenario key for the current pytest execution. |
| `{run_id}` | `4f7a...` | Stable random hex value for the scenario execution. You may set `run_id` in `variables` to control it. |
| `{uuid}` | `550e8400-e29b-41d4-a716-446655440000` | Stable UUID string for the scenario execution. You may set `uuid` in `variables` to control it. |
| `{current_date}` | `2026-07-09` | Current local date in ISO format. |
| `{current_timestamp}` | `2026-07-09T12:34:56.123456+00:00` | Current UTC timestamp in ISO format. |

Generated random placeholders:

| Pattern | Example | Output |
| --- | --- | --- |
| `{random_numbers_<length>}` | `{random_numbers_9}` | Digits only, such as `123456789`. |
| `{random_alpha_<length>}` | `{random_alpha_3}` | Uppercase letters only, such as `ABC`. |
| `{random_alpha_numeric_<length>}` | `{random_alpha_numeric_6}` | Uppercase letters and digits, such as `A1B2C3`. |

The `<length>` must be a positive integer. A generated random placeholder is stable after its first use in a scenario execution, so `{random_numbers_9}` renders to the same value wherever it appears in that scenario.

Named generated placeholders are also supported when the scenario reads better with a meaningful name:

| Pattern | Example | Output |
| --- | --- | --- |
| `{generated_<name>_numbers_<length>}` | `{generated_order_id_numbers_9}` | Stable digits for that named value. |
| `{generated_<name>_alpha_<length>}` | `{generated_region_alpha_3}` | Stable uppercase letters for that named value. |
| `{generated_<name>_alpha_numeric_<length>}` | `{generated_customer_code_alpha_numeric_6}` | Stable uppercase letters and digits for that named value. |

Placeholder sources are applied in this order:

1. Built-in values.
2. Scenario `variables`.
3. Values captured from earlier destination steps.
4. Values returned by `fabric_placeholder_providers`.

Later sources can override earlier ones. This lets a test framework provide project-specific values while keeping scenario JSON compact.

## Dataverse As An Extension

`examples/dataverse_destination.py` shows a Dataverse destination you can copy or adapt into your test project. Register your project-owned destination with the runner:

```python
import pytest

from fabric_pytester.core.http import BearerHttpClient
from tests.destinations.dataverse import DataverseDestination


SCENARIOS = ["orders_with_dataverse"]


@pytest.mark.fabric_scenarios(SCENARIOS)
def test_orders(fabric_runner, fabric_token_provider):
    http = BearerHttpClient(
        base_url="https://example.dataverse.test",
        token_provider=fabric_token_provider,
        scope="https://example.dataverse.test/.default",
    )
    runner = fabric_runner.add_destination("dataverse", DataverseDestination(http))
    runner.run_scenarios(SCENARIOS)
```

This lets you keep Dataverse-specific records, filters, and assertions in a small Python extension that fits your environment.

## Fixtures

All fixtures provided by the plugin:

| Fixture | What it provides |
| --- | --- |
| `make_random` | A `RandomHelper` for generating ad hoc test names and random strings in Python tests. |
| `fabric_env_name` | Active environment name from `--fabric-env`, `FABRIC_ENV`, or `dev`. |
| `fabric_config` | Loaded `FabricPytesterConfig` for the active environment. |
| `fabric_debug_env` | Local debug secret provider for the active environment. |
| `fabric_credentials` | Chained secret provider selected from local, environment, or Key Vault configuration. |
| `fabric_env_or_skip` | Helper that reads a required secret or skips the test. |
| `fabric_token_provider` | Token provider for Fabric and OneLake authentication. Uses configured token-provider settings. |
| `fabric_http` | Bearer HTTP client for the Fabric API. |
| `fabric_client` | Fabric job client. Skips if `fabric.workspace_id` is not configured. |
| `onelake_client` | OneLake client. Skips if OneLake config is incomplete. |
| `fabric_sql` | Fabric SQL backend. Skips if Fabric SQL config is incomplete. |
| `fabric_sql_backends` | Lazily created named SQL backends from `environments.<env>.sql_backends`. |
| `fabric_placeholder_providers` | List of project placeholder providers. Override this fixture to add framework values. |
| `fabric_scenario_loader` | Scenario loader using configured scenario paths. |
| `fabric_current_scenario` | Current scenario key selected by `@pytest.mark.fabric_scenarios`, or `None` outside marker-based tests. |
| `fabric_scenario_group_state` | Session state used to share setup and pipeline phases across scenario cases for the same marked test definition. |
| `fabric_runner` | Scenario runner with configured Fabric, OneLake, and named SQL backend destinations. |
| `make_fabric_file` | Helper for uploading a OneLake file from a test and deleting it afterward. |
| `make_fabric_job_run` | Helper for running a Fabric item job from a test. |

## Configuration

Core configuration includes Fabric, OneLake, secrets, scenario paths, artifact paths, and timeouts. Custom destinations should read their own settings from project config, environment variables, or fixtures.

Configuration can live in a standalone `fabric-pytester.toml` file:

```toml
[tool.fabric-pytester]
scenario_paths = ["tests/fabric/scenarios"]
artifact_dir = "results/artifacts/fabric"
```

Discovery order is:

1. `--fabric-config <path>` when supplied.
2. `fabric-pytester.toml` in the pytest root.
3. `[tool.fabric-pytester]` in `pyproject.toml`.

Use `--fabric-config` when a project keeps integration-test configuration outside the root:

```bash
pytest --fabric-config tests/fabric/fabric-pytester.toml
```

This standalone file can be used in addition to the main Python `pyproject.toml`. That keeps Python project metadata and general pytest settings separate from Fabric environment configuration. The example project includes `examples/example_project/fabric-pytester.complete.example.toml` with dev/QA environments, OneLake, secret providers, default Fabric SQL, JDBC, and pyodbc backend examples.

Map the Fabric environment to an existing pytest option when a project already uses one:

```toml
[tool.fabric-pytester]
pytest_env_option = "env"
```

With that configuration, `pytest --env=ci` selects the `ci` Fabric environment. `--fabric-env` still provides an explicit Fabric-specific override, followed by the configured option, `FABRIC_ENV`, and finally `dev`.

Enable debug output when diagnosing a scenario:

```toml
[tool.fabric-pytester]
debug = true
```

or:

```bash
pytest --fabric-debug
```

Debug mode prints the active SQL adapter, SQL host/database, scenario and block names, rendered placeholders, OneLake upload paths, Fabric job ids/statuses, assertion SQL, expected/actual row counts, elapsed time, and sample rows.

Enable SQL connection diagnostics when checking which database an adapter reached:

```toml
[tool.fabric-pytester]
sql_diagnostics = true
```

or:

```bash
pytest --fabric-sql-diagnostics
```

When enabled, each SQL backend runs `SELECT DB_NAME()` after connecting and prints the active adapter plus the returned database name or diagnostic error.

## Secrets And Token Providers

There are two related settings:

| Setting | Purpose |
| --- | --- |
| `secrets.provider` | Where named values such as client IDs and client secrets are read from. |
| `fabric.token_provider` | How Fabric API and OneLake bearer tokens are created. |

Configure the secret provider under the active environment:

```toml
[tool.fabric-pytester.environments.dev.secrets]
provider = "env"
```

In a standalone `fabric-pytester.toml`, omit the `tool.fabric-pytester` prefix:

```toml
[environments.dev.secrets]
provider = "env"
```

Supported secret providers:

| Provider | Use when |
| --- | --- |
| `local` | Local/debug runs should read secrets from the local debug file first, then environment variables. This is the default. |
| `env` | Secrets should come directly from environment variables. |
| `auth_token` | An existing pytest `auth_token` fixture returns a dictionary of secret values. |
| `keyvault` | Secrets should come from Azure Key Vault, with environment variables as fallback. |

For normal Fabric client-credential authentication, the plugin reads these names from the selected provider:

```text
FABRIC_CLIENT_ID
FABRIC_CLIENT_SECRET
FABRIC_TENANT_ID
```

If your project already has those values under different names, map the Fabric name to the project/provider name:

```toml
[tool.fabric-pytester.environments.dev.secrets.aliases]
FABRIC_CLIENT_ID = "project_client_id"
FABRIC_CLIENT_SECRET = "project_client_secret"
FABRIC_TENANT_ID = "project_tenant_id"
```

That means: when `fabric-pytester` asks for `FABRIC_CLIENT_ID`, read `project_client_id` from the configured provider instead. For example, with `provider = "auth_token"`:

```python
@pytest.fixture
def auth_token():
    return {
        "project_client_id": "...",
        "project_client_secret": "...",
        "project_tenant_id": "...",
    }
```

Use the same `aliases` section with Key Vault when the vault secret names differ:

```toml
[tool.fabric-pytester.environments.dev.secrets]
provider = "keyvault"
vault_url = "https://<vault-name>.vault.azure.net/"

[tool.fabric-pytester.environments.dev.secrets.aliases]
FABRIC_CLIENT_ID = "fabric-client-id"
FABRIC_CLIENT_SECRET = "fabric-client-secret"
FABRIC_TENANT_ID = "fabric-tenant-id"
```

`auth_token` is only used as a secrets source when `secrets.provider = "auth_token"`. That keeps projects with an unrelated `auth_token` fixture from being picked up accidentally.

Set `fabric.token_provider` when you want to control how Fabric API and OneLake access tokens are created. Use an existing token fixture when your test framework already creates one:

```toml
[tool.fabric-pytester.environments.dev.fabric]
token_provider = "auth_token"
```

The fixture may return a token string, a callable, or an object with `get_token(scope)`.

Use a tenant-specific OAuth token URL:

```toml
[tool.fabric-pytester.environments.dev.fabric]
token_provider = "client_credentials_url"
auth_url = "https://login.microsoftonline.com/<tenant>/oauth2/v2.0/token"
grant_type = "client_credentials"
```

With `client_credentials_url`, the tenant is already in `auth_url`, so only `FABRIC_CLIENT_ID` and `FABRIC_CLIENT_SECRET` are required unless you override `client_id_secret` or `client_secret_secret`.

Fabric job API style can be configured instead of passed on the command line:

```toml
[tool.fabric-pytester.environments.dev.fabric]
job_api_style = "query"
```
