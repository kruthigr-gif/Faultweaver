import asyncio

import loadgen.run_load as run_load


class FakeResponse:
    def __init__(self, token_data):
        self.token_data = token_data

    def raise_for_status(self):
        return None

    def json(self):
        return self.token_data


class FakeClient:
    def __init__(self, token_data):
        self.token_data = token_data
        self.requests = []

    async def post(self, url, data):
        self.requests.append((url, data))
        return FakeResponse(self.token_data)


def test_token_provider_refreshes_expiring_access_token(monkeypatch):
    monkeypatch.setattr(run_load.time, "monotonic", lambda: 1000)
    client = FakeClient(
        {
            "access_token": "refreshed-access",
            "refresh_token": "rotated-refresh",
            "expires_in": 300,
        }
    )
    provider = run_load.TokenProvider(
        {
            "access_token": "initial-access",
            "refresh_token": "initial-refresh",
            "expires_in": 300,
        },
        client,
    )
    provider.expires_at = 1000

    token = asyncio.run(provider.get_access_token())

    assert token == "refreshed-access"
    assert provider.refresh_token == "rotated-refresh"
    assert client.requests == [
        (
            run_load.TOKEN_URL,
            {
                "grant_type": "refresh_token",
                "client_id": "sre-api",
                "refresh_token": "initial-refresh",
            },
        )
    ]
