from fabric_pytester.core.onelake_client import AzureTokenCredential, OneLakeClient
from fabric_pytester.core.tokens import ONELAKE_SCOPE, StaticTokenProvider


def test_azure_token_credential_adapts_project_token_provider():
    credential = AzureTokenCredential(StaticTokenProvider("one-lake-token"))

    token = credential.get_token(ONELAKE_SCOPE)

    assert token.token == "one-lake-token"
    assert token.expires_on > 0


def test_onelake_records_serialize_as_compact_json():
    data = OneLakeClient._serialize(content=None, records=[{"id": 1, "status": "Submitted"}])

    assert data == b'[{"id": 1, "status": "Submitted"}]'
