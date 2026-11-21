import asyncio
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from investigator.auth import require_user


def test_auth_disabled_allows_request(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    assert asyncio.run(require_user(None)) is None


def test_auth_enabled_requires_bearer(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(require_user(None))
    assert exc.value.status_code == 401


def test_auth_enabled_rejects_malformed_token(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("OIDC_ISSUER", "http://localhost:8081/realms/sre-local")
    monkeypatch.setenv("OIDC_AUDIENCE", "sre-api")
    monkeypatch.setenv(
        "OIDC_JWKS_URL",
        "http://localhost/realms/sre-local/protocol/openid-connect/certs",
    )
    monkeypatch.setattr("investigator.auth._jwks", lambda *_: {"keys": []})
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="not-a-jwt")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(require_user(credentials))
    assert exc.value.status_code == 401


def test_auth_enabled_returns_verified_claims(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("OIDC_ISSUER", "http://localhost:8081/realms/sre-local")
    monkeypatch.setenv("OIDC_AUDIENCE", "sre-api")
    monkeypatch.setenv(
        "OIDC_JWKS_URL",
        "http://localhost:8081/realms/sre-local/protocol/openid-connect/certs",
    )
    monkeypatch.setattr("investigator.auth._jwks", lambda *_: {"keys": ["test-key"]})
    monkeypatch.setattr(
        "investigator.auth.jwt.decode",
        lambda token, key_set, **kwargs: {"sub": "local-operator"},
    )
    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials="verified-test-token",
    )

    claims = asyncio.run(require_user(credentials))

    assert claims == {"sub": "local-operator"}
