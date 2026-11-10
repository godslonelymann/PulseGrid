import asyncio
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from conftest import seed
from pulsegrid.api import email_auth
from pulsegrid.api.main import app
from pulsegrid.auth.security import cfg, digest, redis
from pulsegrid.storage.db import Session
from pulsegrid.storage.models import User
from sqlalchemy import select
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

AUTH = "/api/v1/auth"
EMAIL = "new@example.com"
PASSWORD = "original-password-123"
REPLACEMENT = "replacement-password-123"


@pytest.fixture
def inbox(monkeypatch):
    monkeypatch.setattr(cfg, "smtp_host", "test-smtp")
    monkeypatch.setattr(cfg, "smtp_from", "pulsegrid@example.com")
    messages = []

    async def send(message, **options):
        assert options["hostname"] == "test-smtp"
        assert options["start_tls"] == cfg.smtp_starttls
        url = next(line for line in message.get_content().splitlines() if line.startswith("http"))
        link = urlsplit(url)
        assert link.path == "/auth/email" and not link.query
        messages.append((str(message["To"]), parse_qs(link.fragment)))

    monkeypatch.setattr(email_auth.aiosmtplib, "send", send)
    return messages


async def post(client, path, **data):
    if "pg_csrf" not in client.cookies:
        await client.get(AUTH + "/csrf")
    return await client.post(
        AUTH + path, json=data, headers={"origin": cfg.public_origin, "x-csrf-token": client.cookies["pg_csrf"]}
    )


async def test_signup_confirms_inbox_before_creating_password_account(client, inbox, monkeypatch):
    monkeypatch.setattr(cfg, "enable_legacy_password_auth", False)
    assert (await post(client, "/email/signup", email=EMAIL.upper())).status_code == 202
    assert (await post(client, "/login", email=EMAIL, password=PASSWORD)).status_code == 401
    async with Session() as session:
        assert await session.scalar(select(User.id)) is None
    recipient, link = inbox.pop()
    assert recipient == EMAIL and link["flow"] == ["confirm"]
    token = link["token"][0]
    assert 0 < await redis.ttl(f"email-auth:confirm:{digest(token)}") <= 3600

    # The token cannot be repurposed and only one of two simultaneous confirmations can succeed.
    assert (await post(client, "/email/reset-password", token=token, password=PASSWORD)).status_code == 400
    results = await asyncio.gather(*[post(client, "/email/confirm", token=token, password=PASSWORD) for _ in range(2)])
    assert sorted(r.status_code for r in results) == [200, 400]
    assert (await client.get("/api/v1/me")).status_code == 401
    assert (await post(client, "/login", email=EMAIL, password=PASSWORD)).status_code == 200
    assert (await client.get("/api/v1/me")).json()["password_enabled"]
    settings = (await client.get("/api/v1/notification-settings")).json()
    channels = (await client.get("/api/v1/notification-channels")).json()
    assert settings["email_enabled"]
    assert [(channel["id"], channel["email"], channel["enabled"]) for channel in channels] == [
        (settings["email_channel_id"], EMAIL, True)
    ]
    assert (await post(client, "/logout")).status_code == 200
    assert (await client.get("/api/v1/me")).status_code == 401


async def test_reset_expires_invalidates_other_links_and_revokes_existing_sessions(client, inbox):
    await post(client, "/email/signup", email=EMAIL)
    await post(client, "/email/confirm", token=inbox.pop()[1]["token"][0], password=PASSWORD)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as other:
        for browser in (client, other):
            assert (await post(browser, "/login", email=EMAIL, password=PASSWORD)).status_code == 200
        for _ in range(3):
            assert (await post(client, "/email/forgot-password", email=EMAIL)).status_code == 202
        assert (await post(other, "/email/forgot-password", email=EMAIL)).status_code == 429
        expired, valid, superseded = (m[1]["token"][0] for m in inbox)
        key = f"email-auth:reset:{digest(expired)}"
        assert 0 < await redis.ttl(key) <= 1800
        await redis.expire(key, 0)
        assert (await post(other, "/email/reset-password", token=expired, password=REPLACEMENT)).status_code == 400
        assert (await post(other, "/email/reset-password", token=valid, password=REPLACEMENT)).status_code == 200
        for token in (valid, superseded):
            assert (await post(other, "/email/reset-password", token=token, password=PASSWORD)).status_code == 400
        for browser in (client, other):
            assert (await browser.get("/api/v1/me")).status_code == 401
            assert (await post(browser, "/refresh")).status_code == 401
        assert (await post(client, "/login", email=EMAIL, password=PASSWORD)).status_code == 401
        assert (await post(client, "/login", email=EMAIL, password=REPLACEMENT)).status_code == 200


async def test_email_requests_cannot_replace_existing_accounts(client, inbox):
    await seed(EMAIL)
    async with Session.begin() as session:
        session.add(User(email="google@example.com", google_sub="google-sub"))
    for email in (EMAIL, "google@example.com", "missing@example.com"):
        reset = await post(client, "/email/forgot-password", email=email)
        assert reset.status_code == 202
        signup = await post(client, "/email/signup", email=email)
        assert signup.status_code == 202
    assert [message[0] for message in inbox] == ["missing@example.com"]


async def test_forwarded_ip_is_trusted_only_from_caddy_and_limits_are_separate(monkeypatch):
    monkeypatch.setattr(cfg, "google_client_id", "test-client")
    monkeypatch.setattr(cfg, "google_client_secret", "test-secret")
    proxied = ProxyHeadersMiddleware(app, trusted_hosts="127.0.0.1")
    ip_a, ip_b = "192.0.2.1", "192.0.2.2"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=proxied, client=("127.0.0.1", 1234)), base_url="http://test"
    ) as caddy:
        caddy.headers["X-Forwarded-For"] = ip_a
        for _ in range(30):
            assert (await post(caddy, "/google/start")).status_code == 200
        assert (await post(caddy, "/google/start")).status_code == 429
        caddy.headers["X-Forwarded-For"] = ip_b
        assert (await post(caddy, "/google/start")).status_code == 200

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=proxied, client=(ip_a, 1234)), base_url="http://test"
    ) as direct:
        direct.headers["X-Forwarded-For"] = ip_b
        assert (await post(direct, "/google/start")).status_code == 429


async def test_closed_signups_block_new_accounts_and_pending_links(client, inbox, monkeypatch):
    assert (await post(client, "/email/signup", email=EMAIL)).status_code == 202
    token = inbox.pop()[1]["token"][0]
    monkeypatch.setattr(cfg, "signups_enabled", False)
    # A new request and a link sent before the switch are both refused.
    assert (await post(client, "/email/signup", email="other@example.com")).status_code == 403
    assert (await post(client, "/email/confirm", token=token, password=PASSWORD)).status_code == 403
    assert not inbox
    async with Session() as session:
        assert await session.scalar(select(User.id)) is None
