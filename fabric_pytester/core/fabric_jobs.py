from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from fabric_pytester.core.errors import FabricJobError
from fabric_pytester.core.http import BearerHttpClient

TERMINAL_SUCCESS = {"Completed", "Succeeded"}
TERMINAL_FAILURE = {"Failed", "Cancelled", "Canceled"}
LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class FabricItem:
    id: str
    display_name: str
    type: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class FabricJobRun:
    client: FabricClient
    workspace_id: str
    item_id: str
    item_name: str | None
    job_type: str
    instance_id: str
    location: str | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)

    def status(self) -> dict[str, Any]:
        return self.client.get_job_instance(self.item_id, self.job_type, self.instance_id)

    def wait(
        self, *, timeout_seconds: int | None = None, poll_interval_seconds: int | None = None
    ) -> dict[str, Any]:
        return self.client.poll_job(
            self.item_id,
            self.job_type,
            self.instance_id,
            item_name=self.item_name,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )


@dataclass
class FabricClient:
    http: BearerHttpClient
    workspace_id: str
    job_api_style: str = "path"
    default_timeout_seconds: int = 3600
    default_poll_interval_seconds: int = 10

    def list_items(self) -> list[FabricItem]:
        payload = self.http.get_json(f"/v1/workspaces/{self.workspace_id}/items")
        values = payload.get("value", payload if isinstance(payload, list) else [])
        return [
            FabricItem(
                id=str(item.get("id")),
                display_name=str(
                    item.get("displayName") or item.get("display_name") or item.get("name")
                ),
                type=item.get("type"),
                raw=item,
            )
            for item in values
        ]

    def resolve_item(
        self, *, item_id: str | None = None, item_name: str | None = None
    ) -> FabricItem:
        if item_id:
            return FabricItem(id=item_id, display_name=item_name or item_id)
        if not item_name:
            raise FabricJobError("Either item_id or item_name is required")
        matches = [item for item in self.list_items() if item.display_name == item_name]
        if not matches:
            raise FabricJobError(
                f"No Fabric item named {item_name!r} found in workspace {self.workspace_id}"
            )
        if len(matches) > 1:
            ids = ", ".join(item.id for item in matches)
            raise FabricJobError(f"Fabric item name {item_name!r} is ambiguous: {ids}")
        return matches[0]

    def run_item_job(
        self,
        *,
        item_id: str | None = None,
        item_name: str | None = None,
        job_type: str = "Pipeline",
        parameters: dict[str, Any] | None = None,
    ) -> FabricJobRun:
        item = self.resolve_item(item_id=item_id, item_name=item_name)
        path = self._run_path(item.id, job_type)
        payload = self._payload(parameters)
        response = self.http.post_json(path, payload, expected=(200, 201, 202))
        instance_id = self._instance_id(response.headers.get("Location"), response.json())
        if not instance_id:
            raise FabricJobError("Fabric job response did not include a job instance id")
        return FabricJobRun(
            client=self,
            workspace_id=self.workspace_id,
            item_id=item.id,
            item_name=item.display_name,
            job_type=job_type,
            instance_id=instance_id,
            location=response.headers.get("Location"),
            raw_response=response.json() if isinstance(response.json(), dict) else {},
        )

    def get_job_instance(self, item_id: str, job_type: str, instance_id: str) -> dict[str, Any]:
        path = f"{self._job_path(item_id, job_type)}/instances/{instance_id}"
        return self.http.get_json(path)

    def poll_job(
        self,
        item_id: str,
        job_type: str,
        instance_id: str,
        *,
        item_name: str | None = None,
        timeout_seconds: int | None = None,
        poll_interval_seconds: int | None = None,
    ) -> dict[str, Any]:
        timeout_seconds = timeout_seconds or self.default_timeout_seconds
        poll_interval_seconds = poll_interval_seconds or self.default_poll_interval_seconds
        started = time.monotonic()
        deadline = time.monotonic() + timeout_seconds
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            last = self.get_job_instance(item_id, job_type, instance_id)
            status = str(last.get("status") or last.get("state") or "")
            elapsed = time.monotonic() - started
            LOGGER.info(
                "Fabric job poll item=%s item_id=%s instance_id=%s status=%s elapsed=%.1fs",
                item_name or "<unknown>",
                item_id,
                instance_id,
                status or "<unknown>",
                elapsed,
            )
            if status in TERMINAL_SUCCESS:
                return last
            if status in TERMINAL_FAILURE:
                detail = _failure_detail(last)
                raise FabricJobError(
                    "Fabric job "
                    f"{instance_id} for item {item_name or item_id} ended with status {status}. "
                    f"Failure details: {detail}. Response payload: {last}"
                )
            time.sleep(poll_interval_seconds)
        raise FabricJobError(
            f"Fabric job {instance_id} timed out after {timeout_seconds}s. "
            f"Last known status: {last.get('status') or last.get('state') or '<unknown>'}. "
            f"Last Fabric response payload: {last}"
        )

    def _run_path(self, item_id: str, job_type: str) -> str:
        if self.job_api_style == "query":
            return f"{self._item_path(item_id)}/jobs/instances?jobType={quote(job_type)}"
        return f"{self._job_path(item_id, job_type)}/instances"

    def _item_path(self, item_id: str) -> str:
        return f"/v1/workspaces/{self.workspace_id}/items/{item_id}"

    def _job_path(self, item_id: str, job_type: str) -> str:
        return f"{self._item_path(item_id)}/jobs/{quote(job_type)}"

    @staticmethod
    def _payload(parameters: dict[str, Any] | None) -> dict[str, Any] | None:
        if not parameters:
            return None
        return {"executionData": {"parameters": parameters}}

    @staticmethod
    def _instance_id(location: str | None, payload: Any) -> str | None:
        if isinstance(payload, dict):
            for key in ("id", "jobInstanceId", "instanceId"):
                if payload.get(key):
                    return str(payload[key])
        if not location:
            return None
        match = re.search(r"/instances/([^/?#]+)", location)
        return match.group(1) if match else location.rstrip("/").split("/")[-1]


def _failure_detail(payload: dict[str, Any]) -> Any:
    for key in (
        "failureReason",
        "failure_reason",
        "failureDetails",
        "failureInfo",
        "error",
        "errorDetails",
        "details",
        "message",
    ):
        if payload.get(key):
            return payload[key]
    return payload
