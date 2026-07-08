import logging
from dataclasses import dataclass

import pytest

from fabric_pytester.core.credentials import MappingSecretProvider
from fabric_pytester.core.fabric_jobs import FabricClient, FabricJobError
from fabric_pytester.core.http import BearerHttpClient
from fabric_pytester.core.tokens import ClientCredentialsTokenProvider, StaticTokenProvider


@dataclass
class Token:
    token: str
    expires_on: int = 9999999999


class Credential:
    def __init__(self):
        self.calls = 0

    def get_token(self, scope):
        self.calls += 1
        return Token(f"token-{self.calls}")


class Response:
    def __init__(self, status_code=200, data=None, headers=None, text=""):
        self.status_code = status_code
        self._data = data or {}
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._data


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return self.responses.pop(0)


def test_token_provider_caches_until_forced():
    credential = Credential()
    provider = ClientCredentialsTokenProvider(
        secrets=MappingSecretProvider({}), credential=credential
    )
    assert provider.get_token("scope") == "token-1"
    assert provider.get_token("scope") == "token-1"
    assert provider.get_token("scope", force_refresh=True) == "token-2"


def test_http_client_refreshes_on_401():
    session = Session([Response(401), Response(200, {"ok": True})])
    client = BearerHttpClient(
        "https://example.test", StaticTokenProvider("token"), "scope", session=session
    )
    assert client.get_json("/path") == {"ok": True}
    assert len(session.requests) == 2


def test_fabric_client_resolves_item_and_runs_path_style_job(monkeypatch):
    session = Session(
        [
            Response(200, {"value": [{"id": "item-1", "displayName": "pipeline"}]}),
            Response(
                202, {"id": "job-1"}, {"Location": "https://example/jobs/Pipeline/instances/job-1"}
            ),
            Response(200, {"status": "Completed"}),
        ]
    )
    client = FabricClient(
        BearerHttpClient("https://fabric.test", StaticTokenProvider(), "scope", session=session),
        workspace_id="workspace",
        default_poll_interval_seconds=0,
    )
    monkeypatch.setattr("fabric_pytester.core.fabric_jobs.time.sleep", lambda _: None)
    run = client.run_item_job(item_name="pipeline", parameters={"x": 1})
    assert run.instance_id == "job-1"
    assert run.wait()["status"] == "Completed"
    assert "/jobs/Pipeline/instances" in session.requests[1][1]


def test_fabric_client_logs_every_job_poll(monkeypatch, caplog):
    session = Session(
        [
            Response(200, {"status": "Running"}),
            Response(200, {"status": "Completed"}),
        ]
    )
    client = FabricClient(
        BearerHttpClient("https://fabric.test", StaticTokenProvider(), "scope", session=session),
        workspace_id="workspace",
        default_poll_interval_seconds=0,
    )
    monkeypatch.setattr("fabric_pytester.core.fabric_jobs.time.sleep", lambda _: None)

    with caplog.at_level(logging.INFO, logger="fabric_pytester.core.fabric_jobs"):
        result = client.poll_job(
            "item-1",
            "Pipeline",
            "job-1",
            item_name="pipeline",
            timeout_seconds=1,
        )

    assert result["status"] == "Completed"
    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 2
    assert "item=pipeline" in messages[0]
    assert "item_id=item-1" in messages[0]
    assert "instance_id=job-1" in messages[0]
    assert "status=Running" in messages[0]
    assert "elapsed=" in messages[0]


def test_fabric_client_fails_immediately_with_failure_details():
    session = Session(
        [
            Response(
                200,
                {
                    "status": "Failed",
                    "failureReason": {"message": "Notebook step failed"},
                },
            ),
        ]
    )
    client = FabricClient(
        BearerHttpClient("https://fabric.test", StaticTokenProvider(), "scope", session=session),
        workspace_id="workspace",
    )

    with pytest.raises(FabricJobError) as exc_info:
        client.poll_job("item-1", "Pipeline", "job-1", item_name="pipeline", timeout_seconds=1)

    message = str(exc_info.value)
    assert "status Failed" in message
    assert "Notebook step failed" in message
    assert "Response payload" in message
    assert not session.responses


def test_fabric_client_timeout_reports_last_status_and_payload(monkeypatch):
    session = Session([Response(200, {"status": "Running", "progress": 50})])
    client = FabricClient(
        BearerHttpClient("https://fabric.test", StaticTokenProvider(), "scope", session=session),
        workspace_id="workspace",
        default_poll_interval_seconds=0,
    )
    times = iter([0, 0, 0, 0, 2])
    monkeypatch.setattr("fabric_pytester.core.fabric_jobs.time.monotonic", lambda: next(times))
    monkeypatch.setattr("fabric_pytester.core.fabric_jobs.time.sleep", lambda _: None)

    with pytest.raises(FabricJobError) as exc_info:
        client.poll_job("item-1", "Pipeline", "job-1", timeout_seconds=1)

    message = str(exc_info.value)
    assert "Last known status: Running" in message
    assert "Last Fabric response payload" in message
    assert "progress" in message


def test_fabric_client_rejects_ambiguous_item_names():
    session = Session(
        [
            Response(
                200,
                {"value": [{"id": "1", "displayName": "dup"}, {"id": "2", "displayName": "dup"}]},
            )
        ]
    )
    client = FabricClient(
        BearerHttpClient("https://fabric.test", StaticTokenProvider(), "scope", session=session),
        "workspace",
    )
    with pytest.raises(FabricJobError, match="ambiguous"):
        client.resolve_item(item_name="dup")
