def test_plugin_fixture_is_discoverable(pytester):
    pytester.makepyfile(
        """
        def test_make_random(make_random):
            value = make_random("item", 6)
            assert value.startswith("item_")
            assert len(value) == 11
        """
    )
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=1)


def test_fabric_token_provider_ignores_auth_token_without_explicit_config(pytester):
    pytester.makepyfile(
        """
        import pytest


        @pytest.fixture
        def auth_token():
            return "existing-token"


        def test_auth_token_fixture_used(fabric_token_provider):
            assert type(fabric_token_provider).__name__ == "ClientCredentialsTokenProvider"
        """
    )
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=1)


def test_fabric_token_provider_uses_auth_token_when_configured(pytester):
    pytester.makepyprojecttoml(
        """
        [tool.fabric-pytester.environments.dev.fabric]
        token_provider = "auth_token"
        """
    )
    pytester.makepyfile(
        """
        import pytest


        @pytest.fixture
        def auth_token():
            return "existing-token"


        def test_auth_token_fixture_used(fabric_token_provider):
            assert fabric_token_provider.get_token("scope") == "existing-token"
        """
    )
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=1)


def test_fabric_credentials_can_use_auth_token_secrets_dict(pytester):
    pytester.makepyprojecttoml(
        """
        [tool.fabric-pytester.environments.dev.secrets]
        provider = "auth_token"
        """
    )
    pytester.makepyfile(
        """
        import pytest


        @pytest.fixture
        def auth_token():
            return {"fabric_client_id": "client"}


        def test_auth_token_secrets_used(fabric_credentials):
            assert fabric_credentials.require("FABRIC_CLIENT_ID") == "client"
        """
    )
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=1)


def test_fabric_client_uses_job_api_style_from_config(pytester):
    pytester.makepyprojecttoml(
        """
        [tool.fabric-pytester.environments.dev.fabric]
        workspace_id = "workspace"
        job_api_style = "query"
        token_provider = "auth_token"
        """
    )
    pytester.makepyfile(
        """
        import pytest


        @pytest.fixture
        def auth_token():
            return "token"


        def test_job_api_style(fabric_client):
            assert fabric_client.job_api_style == "query"
        """
    )
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=1)


def test_fabric_env_can_use_configured_existing_pytest_option(pytester):
    pytester.makepyprojecttoml(
        """
        [tool.fabric-pytester]
        pytest_env_option = "env"
        """
    )
    pytester.makeconftest(
        """
        def pytest_addoption(parser):
            parser.addoption("--env", action="store", default=None)
        """
    )
    pytester.makepyfile(
        """
        def test_env_option_maps_to_fabric_env(fabric_env_name):
            assert fabric_env_name == "ci"
        """
    )
    result = pytester.runpytest("--env=ci", "-q")
    result.assert_outcomes(passed=1)


def test_fabric_env_option_overrides_configured_existing_pytest_option(pytester):
    pytester.makepyprojecttoml(
        """
        [tool.fabric-pytester]
        pytest_env_option = "env"
        """
    )
    pytester.makeconftest(
        """
        def pytest_addoption(parser):
            parser.addoption("--env", action="store", default=None)
        """
    )
    pytester.makepyfile(
        """
        def test_fabric_env_wins(fabric_env_name):
            assert fabric_env_name == "fabric-ci"
        """
    )
    result = pytester.runpytest("--env=project-ci", "--fabric-env=fabric-ci", "-q")
    result.assert_outcomes(passed=1)


def test_debug_and_sql_diagnostics_cli_flags_update_config(pytester):
    pytester.makepyfile(
        """
        def test_debug_flags(fabric_config):
            assert fabric_config.debug is True
            assert fabric_config.sql_diagnostics is True
        """
    )

    result = pytester.runpytest("--fabric-debug", "--fabric-sql-diagnostics", "-q")
    result.assert_outcomes(passed=1)


def test_configured_sql_backends_are_exposed_as_lazy_backends(pytester):
    pytester.makepyprojecttoml(
        """
        [tool.fabric-pytester.environments.dev.fabric]
        token_provider = "auth_token"

        [tool.fabric-pytester.environments.dev.sql_backends.source_jdbc]
        sql_adapter = "jdbc"
        jdbc_url = "jdbc:vendor://example"
        driver_class = "com.vendor.jdbc.Driver"
        """
    )
    pytester.makepyfile(
        """
        import pytest


        @pytest.fixture
        def auth_token():
            return {
                "fabric_client_id": "client",
                "fabric_client_secret": "secret",
            }


        def test_sql_backend_fixture(fabric_sql_backends):
            assert set(fabric_sql_backends) == {"source_jdbc"}
            assert type(fabric_sql_backends["source_jdbc"]).__name__ == "LazySqlBackend"
        """
    )
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=1)


def test_configured_sql_backends_are_registered_on_fabric_runner(pytester):
    pytester.makepyprojecttoml(
        """
        [tool.fabric-pytester]
        scenario_paths = ["scenarios"]

        [tool.fabric-pytester.environments.dev.fabric]
        token_provider = "auth_token"

        [tool.fabric-pytester.environments.dev.sql_backends.source]
        sql_adapter = "jdbc"
        jdbc_url = "jdbc:source://example"
        driver_class = "com.example.Driver"
        """
    )
    pytester.mkdir("scenarios")
    pytester.makefile(
        ".json",
        **{
            "scenarios/orders": """
            {
              "orders": {
                "insert_source_1": {
                  "sql": "insert into source_orders values ('ORD-1')"
                }
              }
            }
            """
        },
    )
    pytester.makepyfile(
        """
        import pytest

        from fabric_pytester.core.sql_backend import SqlBackend


        class FakeBackend(SqlBackend):
            def __init__(self):
                self.executed = []

            def execute(self, sql, params=()):
                self.executed.append(sql)

            def fetch_one(self, sql, params=()):
                return None

            def fetch_all(self, sql, params=()):
                return []


        BACKEND = FakeBackend()


        @pytest.fixture
        def auth_token():
            return "token"


        @pytest.fixture(autouse=True)
        def patch_registry(monkeypatch):
            import fabric_pytester.fixtures.plugin as plugin

            registry = plugin.default_registry()
            registry.register("jdbc", lambda config, secrets, token_provider: BACKEND)
            monkeypatch.setattr(plugin, "default_registry", lambda: registry)


        def test_runner_uses_configured_sql_backend(fabric_runner):
            fabric_runner.run_scenarios(["orders"])

            assert BACKEND.executed == ["insert into source_orders values ('ORD-1')"]
        """
    )
    result = pytester.runpytest("-q")
    result.assert_outcomes(passed=1)


def test_scenario_group_key_removes_only_pytest_parameter_suffix():
    from fabric_pytester.fixtures.plugin import _scenario_group_key

    assert _scenario_group_key("test_file.py::test_name[scenario_a]") == ("test_file.py::test_name")
    assert _scenario_group_key("test_file.py::ClassName::test_name[scenario_a]") == (
        "test_file.py::ClassName::test_name"
    )


def test_fabric_scenarios_marker_parameterizes_fabric_runner(pytester):
    pytester.makepyprojecttoml(
        """
        [tool.fabric-pytester]
        scenario_paths = ["scenarios"]
        """
    )
    pytester.mkdir("scenarios")
    pytester.makefile(
        ".json",
        **{
            "scenarios/orders": """
            {
              "scenario_a": {},
              "scenario_b": {}
            }
            """
        },
    )
    pytester.makepyfile(
        """
        import pytest


        @pytest.mark.fabric_scenarios(["scenario_a", "scenario_b"])
        def test_pipeline_course_results(fabric_runner):
            batch = fabric_runner.run_scenarios(["scenario_a", "scenario_b"])

            assert [item.context.scenario_key for item in batch.executions] == [
                fabric_runner.current_scenario_key
            ]
        """
    )

    result = pytester.runpytest("-vv")

    result.assert_outcomes(passed=2)
    output = result.stdout.str()
    assert "test_pipeline_course_results[scenario_a]" in output
    assert "test_pipeline_course_results[scenario_b]" in output


def test_function_scenario_group_seeds_all_scenarios_before_first_assertion(pytester):
    pytester.makepyprojecttoml(
        """
        [tool.fabric-pytester]
        scenario_paths = ["scenarios"]
        """
    )
    pytester.mkdir("scenarios")
    pytester.makefile(
        ".json",
        **{
            "scenarios/orders": """
            {
              "scenario_a": {
                "insert_api_1": { "target": "/seed/scenario_a", "payload": { "id": "a" } },
                "expected_api_1": { "target": "/assert/scenario_a" }
              },
              "scenario_b": {
                "insert_api_1": { "target": "/seed/scenario_b", "payload": { "id": "b" } },
                "expected_api_1": { "target": "/assert/scenario_b" }
              }
            }
            """
        },
    )
    pytester.makepyfile(
        """
        import pytest


        INSERTS = []
        ASSERTIONS = []
        SCENARIOS = ["scenario_a", "scenario_b"]


        class ApiDestination:
            def insert(self, target, payload):
                INSERTS.append(target)

            def expected(self, target, **kwargs):
                assert len(INSERTS) == 2
                ASSERTIONS.append(target)

            def delete(self, target, **kwargs):
                pass


        @pytest.fixture
        def project_runner(fabric_runner):
            return fabric_runner.add_destination("api", ApiDestination())


        @pytest.mark.fabric_scenarios(SCENARIOS)
        def test_pipeline_course_results(project_runner):
            batch = project_runner.run_scenarios(SCENARIOS)

            assert len(batch.executions) == 1
        """
    )

    result = pytester.runpytest("-vv")

    result.assert_outcomes(passed=2)


def test_fabric_scenarios_marker_parameterizes_test_classes(pytester):
    pytester.makepyprojecttoml(
        """
        [tool.fabric-pytester]
        scenario_paths = ["scenarios"]
        """
    )
    pytester.mkdir("scenarios")
    pytester.makefile(
        ".json",
        **{
            "scenarios/orders": """
            {
              "scenario_a": {},
              "scenario_b": {},
              "scenario_c": {},
              "scenario_d": {},
              "scenario_e": {}
            }
            """
        },
    )
    pytester.makepyfile(
        """
        import pytest


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
                batch = fabric_runner.run_scenarios(SCENARIOS)

                assert [item.context.scenario_key for item in batch.executions] == [
                    fabric_runner.current_scenario_key
                ]
        """
    )

    result = pytester.runpytest("-vv")

    result.assert_outcomes(passed=5)
    output = result.stdout.str()
    for scenario in ("scenario_a", "scenario_b", "scenario_c", "scenario_d", "scenario_e"):
        assert f"TestPipelineCourseResults::test_pipeline_course_results[{scenario}]" in output


def test_class_scenario_group_seeds_all_scenarios_before_first_assertion(pytester):
    pytester.makepyprojecttoml(
        """
        [tool.fabric-pytester]
        scenario_paths = ["scenarios"]
        """
    )
    pytester.mkdir("scenarios")
    pytester.makefile(
        ".json",
        **{
            "scenarios/orders": """
            {
              "scenario_a": {
                "insert_api_1": { "target": "/seed/scenario_a", "payload": { "id": "a" } },
                "expected_api_1": { "target": "/assert/scenario_a" }
              },
              "scenario_b": {
                "insert_api_1": { "target": "/seed/scenario_b", "payload": { "id": "b" } },
                "expected_api_1": { "target": "/assert/scenario_b" }
              },
              "scenario_c": {
                "insert_api_1": { "target": "/seed/scenario_c", "payload": { "id": "c" } },
                "expected_api_1": { "target": "/assert/scenario_c" }
              },
              "scenario_d": {
                "insert_api_1": { "target": "/seed/scenario_d", "payload": { "id": "d" } },
                "expected_api_1": { "target": "/assert/scenario_d" }
              },
              "scenario_e": {
                "insert_api_1": { "target": "/seed/scenario_e", "payload": { "id": "e" } },
                "expected_api_1": { "target": "/assert/scenario_e" }
              }
            }
            """
        },
    )
    pytester.makepyfile(
        """
        import pytest


        INSERTS = []
        ASSERTIONS = []
        SCENARIOS = [
            "scenario_a",
            "scenario_b",
            "scenario_c",
            "scenario_d",
            "scenario_e",
        ]


        class ApiDestination:
            def insert(self, target, payload):
                INSERTS.append(target)

            def expected(self, target, **kwargs):
                assert len(INSERTS) == 5
                ASSERTIONS.append(target)

            def delete(self, target, **kwargs):
                pass


        @pytest.fixture
        def project_runner(fabric_runner):
            return fabric_runner.add_destination("api", ApiDestination())


        class TestPipelineCourseResults:
            @pytest.mark.fabric_scenarios(SCENARIOS)
            def test_pipeline_course_results(self, project_runner):
                batch = project_runner.run_scenarios(SCENARIOS)

                assert len(batch.executions) == 1

            @classmethod
            def teardown_class(cls):
                assert len(INSERTS) == 5
                assert len(ASSERTIONS) == 5
        """
    )

    result = pytester.runpytest("-vv")

    result.assert_outcomes(passed=5)


def test_scenario_group_state_is_isolated_per_test_class(pytester):
    pytester.makepyprojecttoml(
        """
        [tool.fabric-pytester]
        scenario_paths = ["scenarios"]
        """
    )
    pytester.mkdir("scenarios")
    pytester.makefile(
        ".json",
        **{
            "scenarios/orders": """
            {
              "order_a": {
                "insert_api_1": { "target": "/orders/a", "payload": { "id": "a" } },
                "expected_api_1": { "target": "/orders/assert/a" }
              },
              "order_b": {
                "insert_api_1": { "target": "/orders/b", "payload": { "id": "b" } },
                "expected_api_1": { "target": "/orders/assert/b" }
              },
              "return_a": {
                "insert_api_1": { "target": "/returns/a", "payload": { "id": "a" } },
                "expected_api_1": { "target": "/returns/assert/a" }
              },
              "return_b": {
                "insert_api_1": { "target": "/returns/b", "payload": { "id": "b" } },
                "expected_api_1": { "target": "/returns/assert/b" }
              }
            }
            """
        },
    )
    pytester.makepyfile(
        """
        import pytest


        ORDER_SCENARIOS = ["order_a", "order_b"]
        RETURN_SCENARIOS = ["return_a", "return_b"]
        ACTIVE_GROUP = None
        INSERTS = {"orders": [], "returns": []}


        class ApiDestination:
            def insert(self, target, payload):
                INSERTS[ACTIVE_GROUP].append(target)

            def expected(self, target, **kwargs):
                assert len(INSERTS[ACTIVE_GROUP]) == 2

            def delete(self, target, **kwargs):
                pass


        @pytest.fixture
        def project_runner(fabric_runner):
            return fabric_runner.add_destination("api", ApiDestination())


        class TestOrders:
            @pytest.mark.fabric_scenarios(ORDER_SCENARIOS)
            def test_pipeline(self, project_runner):
                global ACTIVE_GROUP
                ACTIVE_GROUP = "orders"
                project_runner.run_scenarios(ORDER_SCENARIOS)


        class TestReturns:
            @pytest.mark.fabric_scenarios(RETURN_SCENARIOS)
            def test_pipeline(self, project_runner):
                global ACTIVE_GROUP
                ACTIVE_GROUP = "returns"
                project_runner.run_scenarios(RETURN_SCENARIOS)
        """
    )

    result = pytester.runpytest("-vv")

    result.assert_outcomes(passed=4)


def test_class_scenario_group_pipeline_failure_fails_each_scenario_without_assertions(pytester):
    pytester.makepyprojecttoml(
        """
        [tool.fabric-pytester]
        scenario_paths = ["scenarios"]
        """
    )
    pytester.mkdir("scenarios")
    pytester.makefile(
        ".json",
        **{
            "scenarios/orders": """
            {
              "scenario_a": {
                "run_api_1": { "target": "/pipeline" },
                "expected_api_1": { "target": "/assert/scenario_a" }
              },
              "scenario_b": {
                "run_api_1": { "target": "/pipeline" },
                "expected_api_1": { "target": "/assert/scenario_b" }
              }
            }
            """
        },
    )
    pytester.makepyfile(
        """
        import pytest


        RUNS = []
        ASSERTIONS = []
        SCENARIOS = ["scenario_a", "scenario_b"]


        class ApiDestination:
            def insert(self, target, payload):
                pass

            def expected(self, target, **kwargs):
                ASSERTIONS.append(target)

            def delete(self, target, **kwargs):
                pass

            def run(self, target, **kwargs):
                RUNS.append(target)
                raise RuntimeError("pipeline failed: bad payload")


        @pytest.fixture
        def project_runner(fabric_runner):
            return fabric_runner.add_destination("api", ApiDestination())


        class TestPipelineCourseResults:
            @pytest.mark.fabric_scenarios(SCENARIOS)
            def test_pipeline_course_results(self, project_runner):
                project_runner.run_scenarios(SCENARIOS)

            @classmethod
            def teardown_class(cls):
                assert RUNS == ["/pipeline"]
                assert ASSERTIONS == []
        """
    )

    result = pytester.runpytest("-vv")

    result.assert_outcomes(failed=2)
    output = result.stdout.str()
    assert "pipeline failed: bad payload" in output
    assert "setup or pipeline phase failed" in output


def test_fabric_scenarios_marker_can_discover_configured_scenarios(pytester):
    pytester.makepyprojecttoml(
        """
        [tool.fabric-pytester]
        scenario_paths = ["scenarios"]
        """
    )
    pytester.mkdir("scenarios")
    pytester.makefile(
        ".json",
        **{
            "scenarios/orders": """
            {
              "scenario_a": {},
              "scenario_b": {}
            }
            """
        },
    )
    pytester.makepyfile(
        """
        import pytest


        @pytest.mark.fabric_scenarios()
        def test_discovered_scenarios(fabric_runner):
            batch = fabric_runner.run_scenarios(["scenario_a", "scenario_b"])

            assert len(batch.executions) == 1
        """
    )

    result = pytester.runpytest("-vv")

    result.assert_outcomes(passed=2)
    output = result.stdout.str()
    assert "test_discovered_scenarios[scenario_a]" in output
    assert "test_discovered_scenarios[scenario_b]" in output


def test_fixtures_list_includes_all_fabric_pytester_fixtures(pytester):
    result = pytester.runpytest("--fixtures", "-q")
    output = result.stdout.str()
    for fixture_name in (
        "make_random",
        "fabric_env_name",
        "fabric_config",
        "fabric_debug_env",
        "fabric_credentials",
        "fabric_env_or_skip",
        "fabric_token_provider",
        "fabric_http",
        "fabric_client",
        "onelake_client",
        "fabric_sql",
        "fabric_sql_backends",
        "fabric_placeholder_providers",
        "fabric_scenario_loader",
        "fabric_current_scenario",
        "fabric_scenario_group_state",
        "fabric_runner",
        "make_fabric_file",
        "make_fabric_job_run",
    ):
        assert fixture_name in output
