from fabric_pytester.core.credentials import MappingSecretProvider
from fabric_pytester.core.tokens import (
    ClientCredentialsUrlTokenProvider,
    ExistingTokenProvider,
    coerce_token_provider,
)


class TokenObject:
    token = "object-token"


class Provider:
    def __init__(self):
        self.scopes = []

    def get_token(self, scope):
        self.scopes.append(scope)
        return TokenObject()


class Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {"access_token": "url-token", "expires_in": 3600}


class Session:
    def __init__(self):
        self.calls = []

    def post(self, url, data, timeout):
        self.calls.append((url, data, timeout))
        return Response()


def test_existing_token_provider_accepts_string_token():
    provider = ExistingTokenProvider("plain-token")
    assert provider.get_token("scope") == "plain-token"


def test_existing_token_provider_accepts_callable_token_fixture():
    provider = ExistingTokenProvider(lambda scope: f"token-for-{scope}")
    assert provider.get_token("fabric") == "token-for-fabric"


def test_coerce_token_provider_keeps_existing_provider():
    source = Provider()
    provider = coerce_token_provider(source)
    assert provider.get_token("fabric") == "object-token"
    assert source.scopes == ["fabric"]


def test_client_credentials_url_token_provider_posts_to_auth_url():
    session = Session()
    provider = ClientCredentialsUrlTokenProvider(
        secrets=MappingSecretProvider(
            {
                "fabric_client_id": "client",
                "fabric_client_secret": "secret",
            }
        ),
        auth_url="https://login.example/tenant/oauth2/v2.0/token",
        client_id_secret="fabric_client_id",
        client_secret_secret="fabric_client_secret",
        session=session,
    )

    assert provider.get_token("scope") == "url-token"
    assert session.calls == [
        (
            "https://login.example/tenant/oauth2/v2.0/token",
            {
                "grant_type": "client_credentials",
                "client_id": "client",
                "client_secret": "secret",
                "scope": "scope",
            },
            30,
        )
    ]
    assert provider.get_token("scope") == "url-token"
    assert len(session.calls) == 1
