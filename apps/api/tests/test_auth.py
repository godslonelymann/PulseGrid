import asyncio

import httpx
import pyotp
from conftest import login, seed
from pulsegrid.api.main import app
from pulsegrid.auth.security import redis
from pulsegrid.storage.db import Session
from pulsegrid.storage.models import LoginSession, RecoveryCode, User
from sqlalchemy import select


async def test_closed_registration_csrf_cookies_logout(owner):
    assert (await owner.post("/api/v1/auth/register", json={})).status_code == 404
    assert (await owner.get("/api/v1/auth/register/config")).status_code == 404
    for path in ("resend", "verify"):
        assert (await owner.post(f"/api/v1/auth/register/{path}", json={})).status_code == 404
    r = await owner.get("/api/v1/me")
    assert r.status_code == 200
    assert "httponly" in (await owner.post("/api/v1/auth/refresh")).headers.get_list("set-cookie")[0].lower()
    owner.headers["x-csrf-token"] = owner.cookies["pg_csrf"]
    assert (
        await owner.post(
            "/api/v1/monitors", headers={"origin": "https://evil.test"}, json={"url": "https://example.com/"}
        )
    ).status_code == 403
    assert (
        await owner.post("/api/v1/monitors", headers={"x-csrf-token": "bad"}, json={"url": "https://example.com/"})
    ).status_code == 403
    assert (await owner.post("/api/v1/auth/logout")).status_code == 200
    assert (await owner.get("/api/v1/me")).status_code == 401


async def enable_mfa(c):
    r = await c.post("/api/v1/auth/mfa/enroll", json={"password": "correct-password-123"})
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    r = await c.post("/api/v1/auth/mfa/confirm", json={"code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200, r.text
    return secret, r.json()["recovery_codes"]


async def test_enrollment_factor_gate_recovery_race(owner):
    secret, codes = await enable_mfa(owner)
    async with Session() as s:
        u = await s.scalar(select(User))
        assert secret not in u.totp_secret
        assert codes[0] not in str((await s.scalars(select(RecoveryCode.digest))).all())
    await owner.post("/api/v1/auth/logout")
    await owner.get("/api/v1/auth/csrf")
    owner.headers["x-csrf-token"] = owner.cookies["pg_csrf"]
    r = await login(owner)
    assert r.json()["mfa_required"]
    assert (await owner.get("/api/v1/me")).status_code == 401
    challenge = r.json()["challenge"]
    # The same one-time code cannot create two sessions even with two challenges.
    second = (await login(owner)).json()["challenge"]
    responses = await asyncio.gather(
        *[owner.post("/api/v1/auth/verify", json={"challenge": ch, "code": codes[0]}) for ch in (challenge, second)]
    )
    assert sorted(r.status_code for r in responses) == [200, 401]
    assert (await owner.get("/api/v1/me")).status_code == 200
    assert (await owner.post("/api/v1/auth/verify", json={"challenge": second, "code": codes[0]})).status_code in (
        401,
        403,
    )


async def test_totp_replay_race(owner):
    secret, _ = await enable_mfa(owner)
    # Verify the next accepted time step, racing two password challenges.
    r1 = await login(owner)
    r2 = await login(owner)
    responses = await asyncio.gather(
        *[
            owner.post(
                "/api/v1/auth/verify",
                json={
                    "challenge": r.json()["challenge"],
                    "code": pyotp.TOTP(secret).at(__import__("time").time() + 30),
                },
            )
            for r in (r1, r2)
        ]
    )
    assert sorted(r.status_code for r in responses) == [200, 401]


async def test_refresh_race_revokes_family(owner):
    old = owner.cookies.get("pg_refresh")
    csrf = owner.cookies.get("pg_csrf")
    headers = {"origin": "http://localhost:3000", "x-csrf-token": csrf}

    async def refresh():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", cookies={"pg_refresh": old, "pg_csrf": csrf}
        ) as c:
            return await c.post("/api/v1/auth/refresh", headers=headers)

    responses = await asyncio.gather(refresh(), refresh())
    assert sorted(r.status_code for r in responses) == [200, 401]
    assert (await owner.get("/api/v1/me")).status_code == 401
    async with Session() as s:
        assert (await s.scalar(select(LoginSession))).revoked_at is not None


async def test_password_and_revocation(owner):
    r = await owner.post(
        "/api/v1/auth/password", json={"password": "correct-password-123", "new_password": "a-new-strong-password"}
    )
    assert r.status_code == 200, r.text
    assert (await owner.get("/api/v1/me")).status_code == 401
    owner.headers["x-csrf-token"] = owner.cookies["pg_csrf"]
    assert (
        await owner.post("/api/v1/auth/login", json={"email": "owner@example.com", "password": "correct-password-123"})
    ).status_code == 401


async def test_rate_limit(client):
    await seed()
    for _ in range(10):
        assert (
            await client.post("/api/v1/auth/login", json={"email": "owner@example.com", "password": "bad"})
        ).status_code == 401
    assert (
        await client.post("/api/v1/auth/login", json={"email": "owner@example.com", "password": "bad"})
    ).status_code == 429


async def test_old_mfa_challenge_invalidated_by_security_change(owner):
    secret, codes = await enable_mfa(owner)
    challenge = (await login(owner)).json()["challenge"]
    r = await owner.post("/api/v1/auth/mfa/disable", json={"password": "correct-password-123", "code": codes[0]})
    assert r.status_code == 200, r.text
    r = await owner.post("/api/v1/auth/verify", json={"challenge": challenge, "code": codes[1]})
    assert r.status_code == 401, r.text


async def test_invalid_credentials_never_echo_secrets(client):
    value = "sensitive-password-" * 20
    r = await client.post("/api/v1/auth/login", json={"email": "owner@example.com", "password": value})
    assert r.status_code == 422 and value not in r.text


async def test_lost_refresh_response_requires_signin(owner):
    old = owner.cookies.get("pg_refresh")
    assert (await owner.post("/api/v1/auth/refresh")).status_code == 200
    owner.headers["x-csrf-token"] = owner.cookies["pg_csrf"]
    owner.cookies.set("pg_refresh", old, domain="test.local", path="/")
    r = await owner.post("/api/v1/auth/refresh")
    assert r.status_code == 401
    assert (await owner.get("/api/v1/me")).status_code == 401


async def test_redis_loss_does_not_reenable_totp_replay(owner):
    secret, _ = await enable_mfa(owner)
    await redis.flushdb()
    r = await login(owner)
    r = await owner.post(
        "/api/v1/auth/verify", json={"challenge": r.json()["challenge"], "code": pyotp.TOTP(secret).now()}
    )
    assert r.status_code == 401
