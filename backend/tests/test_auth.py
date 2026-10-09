import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from pydantic import ValidationError

from app import auth, main
from app.config import Settings


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def test_no_demo_identity_without_explicit_flag(client, monkeypatch):
    monkeypatch.setattr(main.settings, "demo_mode", False)
    assert client.get("/api/session").status_code == 401
    assert client.post("/api/demo/seed").status_code == 401


def test_demo_never_ignores_present_invalid_bearer(client):
    assert client.get("/api/session", headers={"Authorization": "Bearer invalid"}).status_code in {401, 503}


@pytest.mark.parametrize("update", [
    {"aud": "other-app"}, {"iss": "https://attacker.invalid/auth/v1"},
    {"role": "service_role"}, {"exp": 1}, {"sub": "not-a-uuid"},
])
def test_supabase_claims_must_be_verified(client, monkeypatch, signing_key, update):
    monkeypatch.setattr(main.settings, "supabase_url", "https://unit.supabase.co")
    monkeypatch.setattr(auth, "jwks_client", lambda issuer: SimpleNamespace(
        get_signing_key_from_jwt=lambda token: SimpleNamespace(key=signing_key.public_key())))
    claims = {"sub": "00000000-0000-4000-8000-000000000010", "iat": int(time.time()),
              "exp": int(time.time()) + 300, "iss": "https://unit.supabase.co/auth/v1",
              "aud": "authenticated", "role": "authenticated", "email": "user@example.test", **update}
    token = jwt.encode(claims, signing_key, algorithm="RS256")
    assert client.get("/api/session", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_valid_supabase_token_uses_separate_personal_workspace(client, monkeypatch, signing_key):
    demo = client.get("/api/session").json()
    monkeypatch.setattr(main.settings, "supabase_url", "https://unit.supabase.co")
    monkeypatch.setattr(auth, "jwks_client", lambda issuer: SimpleNamespace(
        get_signing_key_from_jwt=lambda token: SimpleNamespace(key=signing_key.public_key())))
    claims = {"sub": "00000000-0000-4000-8000-000000000010", "iat": int(time.time()),
              "exp": int(time.time()) + 300, "iss": "https://unit.supabase.co/auth/v1",
              "aud": "authenticated", "role": "authenticated", "email": "user@example.test"}
    token = jwt.encode(claims, signing_key, algorithm="RS256")
    session = client.get("/api/session", headers={"Authorization": f"Bearer {token}"})
    assert session.status_code == 200
    assert session.json()["organization"]["id"] != demo["organization"]["id"]
    assert session.json()["mode"] == "authenticated"
    assert session.json()["user"]["id"] == claims["sub"]


def test_production_rejects_demo_eager_and_sqlite(client):
    base = {"env": "production", "supabase_url": "https://unit.supabase.co",
            "public_api_url": "https://api.example.test", "web_url": "https://example.test",
            "SPLASH_DEMO_MODE": False, "celery_always_eager": False,
            "database_url": "postgresql+psycopg://u:p@localhost/db"}
    for change in [{"SPLASH_DEMO_MODE": True}, {"celery_always_eager": True},
                   {"database_url": "sqlite:///:memory:"}, {"cors_origins": ["*"]},
                   {"public_api_url": "http://api.example.test"}]:
        with pytest.raises(ValidationError):
            Settings(**{**base, **change})
