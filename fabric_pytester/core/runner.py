from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fabric_pytester.core.assertions import AssertionCollector
from fabric_pytester.core.cleanup import CleanupStack
from fabric_pytester.core.destinations import (
    Destination,
    DestinationStep,
    FabricDestination,
    OneLakeDestination,
)
from fabric_pytester.core.errors import ScenarioError
from fabric_pytester.core.renderer import PlaceholderProvider, ScenarioContext, render
from fabric_pytester.core.scenario_loader import Scenario, ScenarioLoader
from fabric_pytester.core.sql_backend import SqlBackend

LOGGER = logging.getLogger(__name__)


@dataclass
class ScenarioExecution:
    scenario: Scenario
    context: ScenarioContext


@dataclass
class ScenarioBatch:
    executions: list[ScenarioExecution]
    cleanup: CleanupStack = field(default_factory=CleanupStack)
    job_runs: list[Any] = field(default_factory=list)


@dataclass
class ScenarioGroupRun:
    expected_count: int
    runner: ScenarioRunner | None = None
    batch: ScenarioBatch | None = None
    setup_error: BaseException | None = None
    finished_count: int = 0
    cleanup_done: bool = False


@dataclass
class ScenarioGroupState:
    runs: dict[str, ScenarioGroupRun] = field(default_factory=dict)

    def run_for(self, group_key: str, expected_count: int) -> ScenarioGroupRun:
        if group_key not in self.runs:
            self.runs[group_key] = ScenarioGroupRun(expected_count=expected_count)
        run = self.runs[group_key]
        if run.expected_count != expected_count:
            raise ScenarioError(
                f"Scenario group {group_key!r} expected {run.expected_count} item(s), "
                f"then saw {expected_count}"
            )
        return run

    def finish_item(self, group_key: str) -> None:
        run = self.runs.get(group_key)
        if run is None:
            return
        run.finished_count += 1
        if run.finished_count >= run.expected_count:
            self._cleanup_run(group_key, run)

    def _cleanup_run(self, group_key: str, run: ScenarioGroupRun) -> None:
        if run.cleanup_done:
            return
        run.cleanup_done = True
        if run.runner is None or run.batch is None:
            return
        try:
            run.runner.finish_batch(run.batch)
        except Exception as exc:
            raise ScenarioError(f"Scenario group {group_key!r} cleanup failed: {exc}") from exc
        finally:
            run.batch = None

    def cleanup(self) -> None:
        errors: list[str] = []
        for group_key, run in self.runs.items():
            if run.cleanup_done:
                continue
            try:
                self._cleanup_run(group_key, run)
            except Exception as exc:
                errors.append(str(exc))
        if errors:
            raise ScenarioError("Scenario group cleanup failed:\n" + "\n".join(errors))


@dataclass
class ScenarioGroupContext:
    state: ScenarioGroupState
    group_key: str
    expected_count: int

    def run_for(self) -> ScenarioGroupRun:
        return self.state.run_for(self.group_key, self.expected_count)

    def finish_item(self) -> None:
        self.state.finish_item(self.group_key)


@dataclass
class ScenarioRunner:
    loader: ScenarioLoader | None = None
    fabric_client: Any | None = None
    onelake_client: Any | None = None
    fabric_sql: SqlBackend | None = None
    destinations: dict[str, Destination] = field(default_factory=dict)
    placeholder_providers: list[PlaceholderProvider] = field(default_factory=list)
    artifact_dir: Path | None = None
    keep_artifacts: bool = False
    fabric_sql_seconds: float = 300
    fabric_poll_interval_seconds: float = 10
    debug: bool = False
    current_scenario_key: str | None = None
    scenario_group_context: ScenarioGroupContext | None = None

    def __post_init__(self) -> None:
        if self.fabric_client is not None or self.fabric_sql is not None:
            self.destinations.setdefault(
                "fabric",
                FabricDestination(
                    fabric_client=self.fabric_client,
                    fabric_sql=self.fabric_sql,
                    default_sql_timeout_seconds=self.fabric_sql_seconds,
                    default_sql_poll_interval_seconds=self.fabric_poll_interval_seconds,
                    debug=self.debug,
                ),
            )
        if self.onelake_client is not None:
            self.destinations.setdefault(
                "onelake", OneLakeDestination(self.onelake_client, debug=self.debug)
            )

    def add_destination(self, name: str, destination: Destination) -> ScenarioRunner:
        self.destinations[name] = destination
        return self

    def run_scenarios(self, keys: list[str]) -> ScenarioBatch:
        if self.scenario_group_context is not None and self.current_scenario_key:
            return self._run_grouped_scenario(keys)
        keys = self._run_keys(keys)
        batch = self.phase_data(keys)
        try:
            self.phase_pipelines(batch)
            LOGGER.info("Pipelines completed")
            LOGGER.info("Fabric pipeline completed successfully. Starting assertions...")
            self.phase_assertions(batch)
            LOGGER.info("Assertions completed")
        finally:
            self.finish_batch(batch)
        return batch

    def finish_batch(self, batch: ScenarioBatch) -> None:
        cleanup_errors = self._cleanup_batch(batch)
        LOGGER.info("Cleanup completed")
        self._close_fabric_sql()
        if cleanup_errors:
            raise ScenarioError("Cleanup failed:\n" + "\n".join(cleanup_errors))

    def _cleanup_batch(self, batch: ScenarioBatch) -> list[str]:
        if self.keep_artifacts:
            return []
        return batch.cleanup.run()

    def _cleanup_after_failure(self, batch: ScenarioBatch, exc: BaseException, phase: str) -> None:
        cleanup_errors = self._cleanup_batch(batch)
        LOGGER.info("Cleanup completed")
        self._close_fabric_sql()
        if cleanup_errors:
            raise ScenarioError(
                f"{phase} failed: {exc}\nCleanup failed:\n" + "\n".join(cleanup_errors)
            ) from exc

    def _run_grouped_scenario(self, keys: list[str]) -> ScenarioBatch:
        keys = self._validate_group_keys(keys)
        if self.scenario_group_context is None:
            raise ScenarioError("Scenario group context is not configured")
        group_run = self.scenario_group_context.run_for()
        if group_run.setup_error is not None:
            raise ScenarioError(
                "Scenario group setup or pipeline phase failed before assertions: "
                f"{group_run.setup_error}"
            ) from group_run.setup_error
        if group_run.batch is None:
            group_run.runner = self
            try:
                # Marked scenario tests share setup and pipeline phases, but keep
                # assertions scoped to the current pytest scenario item.
                group_run.batch = self.phase_data(keys)
                self.phase_pipelines(group_run.batch)
                LOGGER.info("Pipelines completed")
                LOGGER.info("Fabric pipeline completed successfully. Starting assertions...")
            except Exception as exc:
                group_run.setup_error = exc
                if group_run.batch is not None:
                    try:
                        self.finish_batch(group_run.batch)
                    except Exception as cleanup_exc:
                        LOGGER.error(
                            "Cleanup failed after scenario group setup failure: %s",
                            cleanup_exc,
                        )
                    group_run.batch = None
                raise
        current_batch = self._current_scenario_batch(group_run.batch)
        self.phase_assertions(current_batch)
        LOGGER.info("Assertions completed")
        return current_batch

    def _validate_group_keys(self, keys: list[str]) -> list[str]:
        self._require_current_key(keys)
        return keys

    def _current_scenario_batch(self, batch: ScenarioBatch | None) -> ScenarioBatch:
        if batch is None:
            raise ScenarioError("Scenario group has not been prepared")
        for execution in batch.executions:
            if execution.context.scenario_key == self.current_scenario_key:
                return ScenarioBatch(
                    executions=[execution],
                    cleanup=batch.cleanup,
                    job_runs=batch.job_runs,
                )
        raise ScenarioError(
            f"Prepared scenario group does not include {self.current_scenario_key!r}"
        )

    def _run_keys(self, keys: list[str]) -> list[str]:
        if self.current_scenario_key:
            self._require_current_key(keys)
            return [self.current_scenario_key]
        if len(keys) > 1:
            raise ScenarioError(
                "Multiple scenario keys require @pytest.mark.fabric_scenarios so pytest can "
                "register one test per scenario"
            )
        return keys

    def _require_current_key(self, keys: list[str]) -> None:
        if self.current_scenario_key in keys:
            return
        raise ScenarioError(
            f"Current pytest scenario {self.current_scenario_key!r} is not in the "
            f"requested scenario keys: {keys!r}"
        )

    def phase_data(self, keys: list[str]) -> ScenarioBatch:
        if self.loader is None:
            raise ScenarioError("ScenarioRunner requires a ScenarioLoader to run scenarios")
        scenarios = self.loader.load()
        executions: list[ScenarioExecution] = []
        seen_counts: dict[str, int] = {}
        for key in keys:
            if key not in scenarios:
                raise ScenarioError(f"Unknown scenario key: {key}")
            seen_counts[key] = seen_counts.get(key, 0) + 1
            scenario_key = key if seen_counts[key] == 1 else f"{key}__{seen_counts[key]}"
            scenario = scenarios[key]
            context = ScenarioContext(
                scenario_key=scenario_key,
                variables=scenario.variables,
                providers=self.placeholder_providers,
            )
            executions.append(ScenarioExecution(scenario=scenario, context=context))
        batch = ScenarioBatch(executions=executions)
        try:
            for execution in executions:
                self._run_data_blocks(execution, batch)
                self._write_context_artifact(execution)
        except Exception as exc:
            self._cleanup_after_failure(batch, exc, "Data setup")
            raise
        LOGGER.info("Data setup completed")
        return batch

    def phase_pipelines(self, batch: ScenarioBatch) -> None:
        seen: set[tuple[str, str, str]] = set()
        for execution in batch.executions:
            for job in execution.scenario.data.get("pipelines", []):
                rendered = render(job, execution.context)
                self._run_fabric_job(rendered, batch, seen)
            for step, block in execution.scenario.destination_blocks("run"):
                if step.destination != "fabric":
                    self._run_destination_block(step, block, execution, batch)
                    continue
                rendered = render(block, execution.context)
                self._run_fabric_job(rendered, batch, seen)

    def phase_assertions(self, batch: ScenarioBatch) -> None:
        collector = AssertionCollector()
        for execution in batch.executions:
            blocks = [
                *execution.scenario.destination_blocks("expected"),
                *execution.scenario.destination_blocks("absent"),
            ]
            for step, block in sorted(blocks, key=lambda item: (item[0].order, item[0].key)):
                LOGGER.info(
                    "Starting assertion scenario=%s block=%s destination=%s operation=%s",
                    execution.context.scenario_key,
                    step.key,
                    step.destination,
                    step.operation,
                )
                try:
                    self._run_destination_block(step, block, execution, batch)
                except Exception as exc:
                    collector.add(execution.context.scenario_key, step.key, str(exc))
        collector.raise_if_any()

    def _run_data_blocks(self, execution: ScenarioExecution, batch: ScenarioBatch) -> None:
        blocks = [
            *execution.scenario.destination_blocks("delete"),
            *execution.scenario.destination_blocks("insert"),
        ]
        for step, block in sorted(blocks, key=lambda item: (item[0].order, item[0].key)):
            self._run_destination_block(step, block, execution, batch)

    def _run_destination_block(
        self,
        step: DestinationStep,
        block: dict[str, Any],
        execution: ScenarioExecution,
        batch: ScenarioBatch,
    ) -> Any:
        destination = self._destination(step.destination)
        rendered = render(block, execution.context)
        target = str(rendered.get("target", _default_target(step, rendered)))
        placeholders = execution.context.values()
        _debug_print(
            self.debug,
            f"Scenario={execution.context.scenario_key} block={step.key} "
            f"destination={step.destination} operation={step.operation} "
            f"placeholders={placeholders!r}",
        )

        if step.operation == "insert":
            payload = rendered.get("payload", _payload_without_controls(rendered))
            result = destination.insert(target, payload)
            self._capture_result(execution, rendered.get("capture"), result)
            self._track_cleanup(batch, rendered, result)
            return result
        if step.operation == "expected":
            kwargs = {key: value for key, value in rendered.items() if key != "target"}
            context_expected = getattr(destination, "expected_with_context", None)
            if callable(context_expected):
                return context_expected(
                    target,
                    scenario_key=execution.context.scenario_key,
                    block_key=step.key,
                    destination_name=step.destination,
                    operation=step.operation,
                    placeholders=placeholders,
                    **kwargs,
                )
            return destination.expected(target, **kwargs)
        if step.operation == "absent":
            absent_destination = getattr(destination, "absent", None)
            kwargs = {key: value for key, value in rendered.items() if key != "target"}
            context_absent = getattr(destination, "absent_with_context", None)
            if callable(context_absent):
                return context_absent(
                    target,
                    scenario_key=execution.context.scenario_key,
                    block_key=step.key,
                    destination_name=step.destination,
                    operation=step.operation,
                    placeholders=placeholders,
                    **kwargs,
                )
            if callable(absent_destination):
                return absent_destination(target, **kwargs)
            kwargs.pop("expected_count", None)
            return destination.expected(target, expected_count=0, **kwargs)
        if step.operation == "delete":
            kwargs = {key: value for key, value in rendered.items() if key != "target"}
            result = destination.delete(target, **kwargs)
            self._capture_result(execution, rendered.get("capture"), result)
            return result
        run_destination = getattr(destination, "run", None)
        if step.operation == "run" and callable(run_destination):
            kwargs = {key: value for key, value in rendered.items() if key != "target"}
            result = run_destination(target, **kwargs)
            batch.job_runs.append(result)
            return result
        raise ScenarioError(f"Destination {step.destination!r} cannot run {step.operation!r} steps")

    def _run_fabric_job(
        self,
        rendered: dict[str, Any],
        batch: ScenarioBatch,
        seen: set[tuple[str, str, str]],
    ) -> None:
        destination = self._destination("fabric")
        run_destination = getattr(destination, "run", None)
        if not callable(run_destination):
            raise ScenarioError("Destination 'fabric' cannot run Fabric jobs")
        item_key = str(rendered.get("item_id") or rendered.get("item") or rendered.get("target"))
        job_type = str(rendered.get("job_type", "Pipeline"))
        parameters = json.dumps(rendered.get("parameters", {}), sort_keys=True)
        dedupe_key = (item_key, job_type, parameters)
        if dedupe_key in seen:
            return
        seen.add(dedupe_key)
        run = run_destination(item_key, **rendered)
        batch.job_runs.append(run)

    def _destination(self, name: str) -> Destination:
        if name in self.destinations:
            return self.destinations[name]
        raise ScenarioError(f"Destination {name!r} is not configured")

    def _capture_result(
        self,
        execution: ScenarioExecution,
        capture: Any,
        result: Any,
    ) -> None:
        if not capture:
            return
        if not isinstance(result, dict):
            raise ScenarioError("capture requires a mapping result from the destination")
        public_result = {key: value for key, value in result.items() if not key.startswith("_")}
        if isinstance(capture, str):
            value = public_result.get("path", public_result.get("value"))
            execution.context.capture(capture, value)
            return
        if isinstance(capture, dict):
            for name, key in capture.items():
                execution.context.capture(name, public_result.get(key, public_result.get(name)))
            return
        raise ScenarioError("capture must be a string or object")

    def _track_cleanup(self, batch: ScenarioBatch, rendered: dict[str, Any], result: Any) -> None:
        if rendered.get("cleanup", True) is False:
            return
        if not isinstance(result, dict) or "_cleanup" not in result:
            return
        label, callback = result["_cleanup"]
        batch.cleanup.add(label, callback)

    def _write_context_artifact(self, execution: ScenarioExecution) -> None:
        if self.artifact_dir is None:
            return
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        path = self.artifact_dir / f"{execution.context.scenario_key}.context.json"
        path.write_text(json.dumps(execution.context.values(), indent=2), encoding="utf-8")

    def _close_fabric_sql(self) -> None:
        if self.fabric_sql is None:
            return
        close = getattr(self.fabric_sql, "close", None)
        if callable(close):
            close()


def _default_target(step: DestinationStep, rendered: dict[str, Any]) -> str:
    if "sql" in rendered:
        return "sql"
    if step.destination == "onelake" and (
        "folder" in rendered or "filename" in rendered or "contains" in rendered
    ):
        return "file"
    return step.destination


def _payload_without_controls(rendered: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in rendered.items() if key not in {"target", "capture", "cleanup"}
    }


def _debug_print(enabled: bool, message: str) -> None:
    if enabled:
        print(f"[fabric-pytester] {message}")
