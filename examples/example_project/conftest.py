import os

import pytest
from tests.destinations.dataverse import DataverseDestination

from fabric_pytester.core.http import BearerHttpClient

pytest_plugins = ["fabric_pytester.fixtures.plugin"]


@pytest.fixture
def dataverse_base_url() -> str:
    return os.environ.get("DATAVERSE_BASE_URL", "https://example.dataverse.test")


@pytest.fixture
def dataverse_destination(fabric_token_provider, dataverse_base_url):
    http = BearerHttpClient(
        base_url=dataverse_base_url,
        token_provider=fabric_token_provider,
        scope=f"{dataverse_base_url}/.default",
    )
    return DataverseDestination(http=http)


@pytest.fixture
def project_runner(fabric_runner, dataverse_destination):
    return fabric_runner.add_destination("dataverse", dataverse_destination)
