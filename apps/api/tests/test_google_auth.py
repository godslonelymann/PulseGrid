import base64
import hashlib
import json
import time
import uuid
from types import SimpleNamespace
from urllib.parse import parse_qs, parse_qsl, urlsplit

import httpx
import jwt
import pytest
from conftest import seed
from cryptography.hazmat.primitives.asymmetric import rsa
from pulsegrid.api import google
from pulsegrid.auth.google_migration import approval_key, authorize_google_migration
from pulsegrid.auth.security import cfg, redis
from pulsegrid.storage.db import Session
from pulsegrid.storage.models import Audit, LoginSession, User
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


async def begin(client, provider, path="start", **body):
    await client.get("/api/v1/auth/csrf")
    client.headers.update({"origin": cfg.public_origin, "x-csrf-token": client.cookies["pg_csrf"]})
    response = await client.post(f"/api/v1/auth/google/{path}", json=body or None)
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
    channels = (await client.get("/api/v1/notification-channels")).json()
    assert len(channels) == 1
    assert channels[0]["email"] == me["email"] and channels[0]["enabled"]
    client.headers["x-csrf-token"] = client.cookies["pg_csrf"]
    monitor = await client.post(
        "/api/v1/monitors", json={"url": "http://127.0.0.1:18080/", "name": "Returning monitor"}
    )
    assert monitor.status_code == 201
    assert (await client.put("/api/v1/notification-settings", json={"enabled": False})).is_success
    assert (await client.post("/api/v1/auth/refresh")).status_code == 200
    client.headers["x-csrf-token"] = client.cookies["pg_csrf"]
    assert (await client.post("/api/v1/auth/logout")).status_code == 200
    state = await begin(client, provider)
    # Stable Google subject, rather than the mutable email, identifies returning users.
    provider["claims"]["email"] = "changed.email@gmail.com"
    assert callback_values(await finish(client, state)) == {"google_success": "1"}
    assert (await client.get("/api/v1/me")).json()["id"] == me["id"]
    dashboard = (await client.get("/api/v1/dashboard")).json()
    assert [row["id"] for row in dashboard["monitors"]] == [monitor.json()["id"]]
    assert (await client.get("/api/v1/notification-channels")).json() == [{**channels[0], "enabled": False}]
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


async def test_unconfirmed_legacy_account_requires_legacy_password_auth(owner, monkeypatch):
    monkeypatch.setattr(cfg, "enable_legacy_password_auth", False)
    me = (await owner.get("/api/v1/me")).json()
    assert me["password_enabled"] is False
    for path, body in [
        ("/auth/login", {"email": "owner@example.com", "password": "correct-password-123"}),
        ("/auth/password", {"password": "correct-password-123", "new_password": "new-password-123"}),
        ("/auth/mfa/enroll", {"password": "correct-password-123"}),
        ("/auth/mfa/confirm", {"code": "123456"}),
    ]:
        response = await owner.post("/api/v1" + path, json=body)
        assert response.status_code == 403, response.text


async def test_approved_google_migration_preserves_account_and_monitors(owner, provider, monkeypatch):
    me = (await owner.get("/api/v1/me")).json()
    created = await owner.post("/api/v1/monitors", json={"url": "http://127.0.0.1:18080/", "name": "Keep history"})
    assert created.status_code == 201
    await authorize_google_migration(me["email"])
    assert 0 < await redis.ttl(approval_key(me["email"])) <= 900
    monkeypatch.setattr(cfg, "enable_legacy_password_auth", False)
    state = await begin(owner, provider)
    provider["claims"]["email"] = me["email"]
    assert callback_values(await finish(owner, state)) == {"google_success": "1"}
    signed_in = (await owner.get("/api/v1/me")).json()
    assert signed_in["id"] == me["id"]
    assert signed_in["google_connected"] and not signed_in["password_enabled"]
    assert (await owner.get("/api/v1/monitors/" + created.json()["id"])).status_code == 200
    assert await redis.get(approval_key(me["email"])) is None
    async with Session() as session:
        user = await session.scalar(select(User))
        assert user.password_hash is None and user.google_sub == "google-user-123"
        assert await session.scalar(select(func.count()).select_from(User)) == 1
        old_session = await session.get(LoginSession, uuid.UUID(me["session_id"]))
        assert old_session.revoked_at is not None
    # Approval cannot be reused to replace the newly established Google identity.
    state = await begin(owner, provider)
    provider["claims"].update(email=me["email"], sub="different-google-account")
    assert callback_values(await finish(owner, state)) == {"google_error": "email_exists"}


@pytest.mark.parametrize("invalidate", ["expired", "credentials_changed", "different_account"])
async def test_google_migration_requires_current_approval_for_exact_account(client, provider, invalidate):
    await seed("new.user@gmail.com")
    await authorize_google_migration("new.user@gmail.com")
    if invalidate == "expired":
        await redis.delete(approval_key("new.user@gmail.com"))
    elif invalidate == "credentials_changed":
        async with Session.begin() as session:
            user = await session.scalar(select(User))
            user.password_hash = "changed-after-approval"
    else:
        await seed("other.user@gmail.com")
    state = await begin(client, provider)
    if invalidate == "different_account":
        provider["claims"]["email"] = "other.user@gmail.com"
    assert callback_values(await finish(client, state)) == {"google_error": "email_exists"}
    assert (await client.get("/api/v1/me")).status_code == 401
    async with Session() as session:
        assert all(user.google_sub is None for user in await session.scalars(select(User)))


async def test_google_migration_preserves_existing_mfa(owner, provider, monkeypatch):
    _, recovery = await enable_mfa(owner)
    await authorize_google_migration("owner@example.com")
    monkeypatch.setattr(cfg, "enable_legacy_password_auth", False)
    state = await begin(owner, provider)
    provider["claims"]["email"] = "owner@example.com"
    values = callback_values(await finish(owner, state))
    assert values["google_challenge"]
    assert (await owner.get("/api/v1/me")).status_code == 401
    async with Session() as session:
        user = await session.scalar(select(User))
        assert user.totp_secret and user.google_sub == "google-user-123" and user.password_hash is None
    owner.headers["x-csrf-token"] = owner.cookies["pg_csrf"]
    response = await owner.post(
        "/api/v1/auth/verify", json={"challenge": values["google_challenge"], "code": recovery[0]}
    )
    assert response.status_code == 200
    assert (await owner.get("/api/v1/me")).status_code == 200


async def test_connect_google_requires_password_and_mfa_then_preserves_account(owner, provider):
    me = (await owner.get("/api/v1/me")).json()
    monitor = await owner.post("/api/v1/monitors", json={"url": "http://127.0.0.1:18080/", "name": "Keep history"})
    assert monitor.status_code == 201
    _, recovery = await enable_mfa(owner)
    assert (await owner.post("/api/v1/auth/google/link", json={"password": "wrong-password"})).status_code == 403
    assert (await owner.post("/api/v1/auth/google/link", json={"password": "correct-password-123"})).status_code == 403

    state = await begin(owner, provider, "link", password="correct-password-123", code=recovery[0])
    assert callback_values(await finish(owner, state)) == {"google_link": "wrong_account"}
    state = await begin(owner, provider, "link", password="correct-password-123", code=recovery[1])
    provider["claims"]["email"] = me["email"]
    assert callback_values(await finish(owner, state)) == {"google_link": "connected"}
    linked = (await owner.get("/api/v1/me")).json()
    assert linked["id"] == me["id"] and linked["session_id"] == me["session_id"]
    assert linked["google_connected"] and linked["password_enabled"]
    assert (await owner.get(f"/api/v1/monitors/{monitor.json()['id']}")).is_success


async def test_connect_google_refuses_revoked_session(owner, provider):
    state = await begin(owner, provider, "link", password="correct-password-123")
    provider["claims"]["email"] = "owner@example.com"
    assert (await owner.post("/api/v1/auth/logout")).is_success
    assert callback_values(await finish(owner, state)) == {"google_link": "expired"}
    async with Session() as session:
        assert (await session.scalar(select(User))).google_sub is None


async def test_google_user_must_prove_google_and_confirm_mail_before_adding_password(client, provider, monkeypatch):
    from pulsegrid.api import email_auth
    from pulsegrid.api.main import app

    monkeypatch.setattr(cfg, "enable_legacy_password_auth", False)
    monkeypatch.setattr(cfg, "smtp_host", "test-smtp")
    monkeypatch.setattr(cfg, "smtp_from", "pulsegrid@example.com")
    messages = []

    async def send(message, **_):
        messages.append(message.get_content())

    monkeypatch.setattr(email_auth.aiosmtplib, "send", send)
    state = await begin(client, provider)
    await finish(client, state)
    me = (await client.get("/api/v1/me")).json()
    state = await begin(client, provider, "add-password")
    provider["claims"]["sub"] = "another-google-account"
    assert callback_values(await finish(client, state)) == {"google_link": "wrong_account"}
    assert not messages

    state = await begin(client, provider, "add-password")
    assert callback_values(await finish(client, state)) == {"google_link": "check_email"}
    link = next(line for line in messages[0].splitlines() if line.startswith("http"))
    token = parse_qs(urlsplit(link).fragment)["token"][0]
    body = {"token": token, "password": "added-password-123"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as other:
        state = await begin(other, provider)
        await finish(other, state)
        other.headers["x-csrf-token"] = other.cookies["pg_csrf"]
        assert (await other.post("/api/v1/auth/email/add-password", json=body)).status_code == 403

    assert (await client.post("/api/v1/auth/email/add-password", json=body)).is_success
    assert (await client.post("/api/v1/auth/email/add-password", json=body)).status_code == 400
    assert (await client.post("/api/v1/auth/logout")).is_success
    await client.get("/api/v1/auth/csrf")
    client.headers["x-csrf-token"] = client.cookies["pg_csrf"]
    assert (
        await client.post("/api/v1/auth/login", json={"email": me["email"], "password": body["password"]})
    ).is_success
    linked = (await client.get("/api/v1/me")).json()
    assert linked["id"] == me["id"] and linked["google_connected"] and linked["password_enabled"]


async def test_google_only_account_verifies_with_google_for_mfa_and_password(client, provider):
    import pyotp

    state = await begin(client, provider)
    await finish(client, state)
    me = (await client.get("/api/v1/me")).json()
    assert not me["google_verified"]
    client.headers["x-csrf-token"] = client.cookies["pg_csrf"]
    # Without a fresh Google verification, both security actions stay locked.
    assert (await client.post("/api/v1/auth/mfa/enroll", json={})).status_code == 401
    assert (await client.post("/api/v1/auth/password", json={"new_password": "added-password-123"})).status_code == 401

    state = await begin(client, provider, "reauth")
    provider["claims"]["sub"] = "another-google-account"
    assert callback_values(await finish(client, state)) == {"google_link": "wrong_account"}
    assert not (await client.get("/api/v1/me")).json()["google_verified"]

    state = await begin(client, provider, "reauth")
    assert callback_values(await finish(client, state)) == {"google_link": "verified"}
    assert (await client.get("/api/v1/me")).json()["google_verified"]

    r = await client.post("/api/v1/auth/mfa/enroll", json={})
    assert r.status_code == 200, r.text
    r = await client.post("/api/v1/auth/mfa/confirm", json={"code": pyotp.TOTP(r.json()["secret"]).now()})
    assert r.status_code == 200, r.text
    recovery = r.json()["recovery_codes"]
    assert (await client.get("/api/v1/me")).json()["mfa_enabled"]

    body = {"new_password": "added-password-123"}
    assert (await client.post("/api/v1/auth/password", json=body)).status_code == 401
    r = await client.post("/api/v1/auth/password", json={**body, "code": recovery[0]})
    assert r.status_code == 200, r.text
    async with Session() as session:
        user = await session.scalar(select(User))
        assert user.password_hash and user.email_verified_at and user.google_sub
        assert await session.scalar(select(func.count()).select_from(Audit).where(Audit.kind == "password_added")) == 1

    # The new password works for email sign-in, and the account keeps its authenticator.
    await client.get("/api/v1/auth/csrf")
    client.headers["x-csrf-token"] = client.cookies["pg_csrf"]
    r = await client.post("/api/v1/auth/login", json={"email": me["email"], "password": body["new_password"]})
    assert r.status_code == 200 and r.json()["mfa_required"]
    # A password account confirms changes with its password, not with Google.
    assert (await client.post("/api/v1/auth/google/reauth")).status_code in (401, 409)
