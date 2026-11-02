import base64
import hashlib
import json
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, parse_qsl, urlsplit

import httpx
import jwt
import pytest
from conftest import seed
from cryptography.hazmat.primitives.asymmetric import rsa
from pulsegrid.api import google
from pulsegrid.auth.security import cfg, redis
from pulsegrid.storage.db import Session
from pulsegrid.storage.models import Audit, User
from sqlalchemy import func, select
from test_auth import enable_mfa


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(cfg, "google_client_id", "test-client.apps.googleusercontent.com")
    monkeypatch.setattr(cfg, "google_client_secret", "test-client-secret")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    public["kid"] = "test-key"
    data = {"claims": {}, "status": 200, "signature_key": key, "public": public, "requests": []}

    def handler(request):
        data["requests"].append(request)
        if request.url.path == "/token":
            body = dict(parse_qsl(request.content.decode()))
            assert body["client_id"] == cfg.google_client_id
            assert body["client_secret"] == cfg.google_client_secret
            assert body["redirect_uri"] == "http://localhost:3000/api/v1/auth/google/callback"
            challenge = (
                base64.urlsafe_b64encode(hashlib.sha256(body["code_verifier"].encode()).digest()).rstrip(b"=").decode()
            )
            assert challenge == data["code_challenge"]
            token = jwt.encode(data["claims"], data["signature_key"], algorithm="RS256", headers={"kid": "test-key"})
            return httpx.Response(data["status"], json={"id_token": token})
        assert str(request.url) == "https://www.googleapis.com/oauth2/v3/certs"
        return httpx.Response(200, json={"keys": [data["public"]]})

    monkeypatch.setattr(
        google,
        "httpx",
        SimpleNamespace(
            AsyncClient=lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw),
            HTTPError=httpx.HTTPError,
        ),
    )
    return data


async def begin(client, provider):
    await client.get("/api/v1/auth/csrf")
    client.headers["x-csrf-token"] = client.cookies["pg_csrf"]
    response = await client.post("/api/v1/auth/google/start")
    assert response.status_code == 200, response.text
    params = {k: v[0] for k, v in parse_qs(urlsplit(response.json()["authorization_url"]).query).items()}
    assert params["scope"] == "openid email"
    assert params["code_challenge_method"] == "S256"
    assert "client_secret" not in params
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=lax" in response.headers["set-cookie"].lower()
    provider["code_challenge"] = params["code_challenge"]
    provider["claims"] = {
        "iss": "https://accounts.google.com",
        "aud": cfg.google_client_id,
        "sub": "google-user-123",
        "email": "new.user@gmail.com",
        "email_verified": True,
        "nonce": params["nonce"],
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
    }
    return params["state"]


async def finish(client, state, **params):
    return await client.get("/api/v1/auth/google/callback", params={"state": state, "code": "google-code", **params})


def callback_values(response):
    assert response.status_code == 303, response.text
    assert response.headers["cache-control"] == "no-store"
    return dict(parse_qsl(urlsplit(response.headers["location"]).fragment))


async def test_google_disabled_without_credentials(client, monkeypatch):
    monkeypatch.setattr(cfg, "google_client_id", "")
    monkeypatch.setattr(cfg, "google_client_secret", "")
    assert (await client.get("/api/v1/auth/google/config")).json() == {"enabled": False}
    assert (await client.post("/api/v1/auth/google/start")).status_code == 503


async def test_google_signup_session_refresh_and_returning_signin(client, provider):
    state = await begin(client, provider)
    assert callback_values(await finish(client, state)) == {"google_success": "1"}
    me = (await client.get("/api/v1/me")).json()
    assert me["email"] == "new.user@gmail.com"
    assert me["google_connected"] and not me["password_enabled"]
    client.headers["x-csrf-token"] = client.cookies["pg_csrf"]
    assert (await client.post("/api/v1/auth/refresh")).status_code == 200
    client.headers["x-csrf-token"] = client.cookies["pg_csrf"]
    assert (await client.post("/api/v1/auth/logout")).status_code == 200
    state = await begin(client, provider)
    # Stable Google subject, rather than the mutable email, identifies returning users.
    provider["claims"]["email"] = "changed.email@gmail.com"
    assert callback_values(await finish(client, state)) == {"google_success": "1"}
    assert (await client.get("/api/v1/me")).json()["id"] == me["id"]
    async with Session() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 1
        assert await session.scalar(select(func.count()).select_from(Audit).where(Audit.kind == "google_signup")) == 1


@pytest.mark.parametrize("attack", ["state", "cookie", "expired", "replay"])
async def test_google_state_is_browser_bound_and_single_use(client, provider, attack):
    state = await begin(client, provider)
    if attack == "state":
        state = "untrusted-state"
    elif attack == "cookie":
        client.cookies.delete(google.COOKIE)
    elif attack == "expired":
        await redis.delete(f"google-state:{google.digest(state)}")
    else:
        assert callback_values(await finish(client, state, error="access_denied")) == {"google_error": "cancelled"}
    assert callback_values(await finish(client, state)) == {"google_error": "expired"}
    assert not provider["requests"]
    assert (await client.get("/api/v1/me")).status_code == 401


@pytest.mark.parametrize(
    ("claim", "value"),
    [
        ("nonce", "wrong"),
        ("aud", "another-client"),
        ("iss", "https://evil.test"),
        ("exp", 1),
        ("email_verified", False),
        ("azp", "another-client"),
        ("email", "invalid"),
        ("sub", ""),
    ],
)
async def test_google_rejects_invalid_identity(client, provider, claim, value):
    state = await begin(client, provider)
    provider["claims"][claim] = value
    assert callback_values(await finish(client, state)) == {"google_error": "failed"}
    assert (await client.get("/api/v1/me")).status_code == 401
    async with Session() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 0


async def test_google_rejects_forged_signature_and_unknown_key(client, provider):
    state = await begin(client, provider)
    provider["signature_key"] = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert callback_values(await finish(client, state)) == {"google_error": "failed"}
    state = await begin(client, provider)
    provider["public"]["kid"] = "unknown"
    assert callback_values(await finish(client, state)) == {"google_error": "failed"}


async def test_google_provider_failure_is_safe(client, provider):
    state = await begin(client, provider)
    provider["status"] = 503
    response = await finish(client, state)
    assert callback_values(response) == {"google_error": "failed"}
    assert cfg.google_client_secret not in response.text
    assert (await client.get("/api/v1/me")).status_code == 401


async def test_google_does_not_take_over_existing_password_account(client, provider):
    await seed("new.user@gmail.com")
    state = await begin(client, provider)
    assert callback_values(await finish(client, state)) == {"google_error": "email_exists"}
    async with Session() as session:
        user = await session.scalar(select(User))
        assert user.google_sub is None and user.password_hash


async def test_google_does_not_bypass_local_mfa(owner, provider):
    _, recovery = await enable_mfa(owner)
    async with Session.begin() as session:
        user = await session.scalar(select(User))
        user.google_sub = "google-user-123"
    assert (await owner.post("/api/v1/auth/logout")).status_code == 200
    state = await begin(owner, provider)
    response = await finish(owner, state)
    challenge = callback_values(response)["google_challenge"]
    assert (await owner.get("/api/v1/me")).status_code == 401
    owner.headers["x-csrf-token"] = owner.cookies["pg_csrf"]
    response = await owner.post("/api/v1/auth/verify", json={"challenge": challenge, "code": recovery[0]})
    assert response.status_code == 200, response.text
    assert (await owner.get("/api/v1/me")).status_code == 200


async def test_google_only_accounts_cannot_use_password_endpoints(client, provider):
    state = await begin(client, provider)
    await finish(client, state)
    client.headers["x-csrf-token"] = client.cookies["pg_csrf"]
    assert (await client.post("/api/v1/auth/mfa/enroll", json={"password": "anything"})).status_code == 401
    assert (
        await client.post("/api/v1/auth/password", json={"password": "anything", "new_password": "long-password-123"})
    ).status_code == 401
    assert (
        await client.post("/api/v1/auth/login", json={"email": "new.user@gmail.com", "password": "anything"})
    ).status_code == 401


async def test_google_start_requires_csrf(client, provider):
    assert (await client.post("/api/v1/auth/google/start", headers={"x-csrf-token": "wrong"})).status_code == 403
