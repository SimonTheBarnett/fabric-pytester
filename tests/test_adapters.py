from typing import cast

from examples.dataverse_destination import DataverseDestination, entity_id_from_headers
from fabric_pytester.core.http import BearerHttpClient


class Response:
    headers = {"OData-EntityId": "https://example/accounts(abc)"}

    def json(self):
        return {"name": "Example Account"}


class Http:
    def __init__(self):
        self.payload = None

    def post_json(self, path, payload):
        self.path = path
        self.payload = payload
        return Response()


def test_entity_id_from_headers():
    assert entity_id_from_headers({"OData-EntityId": "https://example/accounts(abc)"}) == "abc"


def test_dataverse_destination_uses_payload_builder_and_captures_entity_id():
    http = Http()
    client = DataverseDestination(
        http=cast(BearerHttpClient, http),
        endpoint_prefix="/api/data/v9.2",
        payload_builders={"accounts": lambda p: {"name": p["name"].upper()}},
    )
    result = client.insert("accounts", {"name": "example account"})
    assert http.path == "api/data/v9.2/accounts"
    assert http.payload == {"name": "EXAMPLE ACCOUNT"}
    assert result["@entity_id"] == "abc"
