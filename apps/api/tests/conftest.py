import os

# Explicitly isolated from both development and hour-long capacity data.
os.environ["DATABASE_URL"] = "postgresql+psycopg://pulsegrid:test-only@localhost:55433/pulsegrid_test"
os.environ["REDIS_URL"] = "redis://localhost:56380/0"
os.environ["PRIVATE_TARGET_ALLOWLIST"] = "127.0.0.1:18080,127.0.0.1:18081,localhost:18080"
# Local SMTP fixtures must not inherit the operator's real provider credentials.
os.environ.update(SMTP_HOST="", SMTP_USER="", SMTP_PASSWORD="", SMTP_FROM="")
# Existing security regressions exercise legacy credentials only in the isolated test profile.
os.environ["ENABLE_LEGACY_PASSWORD_AUTH"] = "true"

import httpx
import pytest_asyncio
from pulsegrid.api.main import app
from pulsegrid.auth.security import hasher, redis
from pulsegrid.storage.db import Session, engine
from pulsegrid.storage.models import User
from sqlalchemy import text


@pytest_asyncio.fixture(autouse=True)
async def clean():
    async with engine.begin() as c:
        await c.execute(text("TRUNCATE users, worker_heartbeats CASCADE"))
    await redis.flushdb()
    yield
    await engine.dispose()
    await redis.aclose()


@pytest_asyncio.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=True), base_url="http://test"
    ) as c:
        await c.get("/api/v1/auth/csrf")
        c.headers.update({"origin": "http://localhost:3000", "x-csrf-token": c.cookies["pg_csrf"]})
        yield c


async def seed(email="owner@example.com"):
    async with Session.begin() as s:
        u = User(email=email, password_hash=hasher.hash("correct-password-123"))
        s.add(u)
        await s.flush()
        return u.id


async def login(c, email="owner@example.com"):
    r = await c.post("/api/v1/auth/login", json={"email": email, "password": "correct-password-123"})
    assert r.status_code == 200, r.text
    c.headers["x-csrf-token"] = c.cookies["pg_csrf"]
    return r


@pytest_asyncio.fixture
async def owner(client):
    await seed()
    await login(client)
    return client


@pytest_asyncio.fixture
async def fixture_server():
    import asyncio

    state = {"status": 200, "delay": 0, "hits": 0, "payloads": [], "body": False}

    async def handler(reader, writer):
        try:
            headers = await reader.readuntil(b"\r\n\r\n")
            length = next(
                (int(x.split(b":", 1)[1]) for x in headers.split(b"\r\n") if x.lower().startswith(b"content-length:")),
                0,
            )
            if length:
                state["payloads"].append(await reader.readexactly(length))
            state["hits"] += 1
            await asyncio.sleep(state["delay"])
            status = state["status"]
            writer.write(
                f"HTTP/1.1 {status} Test\r\nContent-Length: {1000000000 if state['body'] else 2}\r\nLocation: http://169.254.169.254/\r\n\r\n".encode()
            )
            await writer.drain()
            if state["body"]:
                await asyncio.sleep(3)
            else:
                writer.write(b"OK")
                await writer.drain()
        except (OSError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    server = await asyncio.start_server(handler, "127.0.0.1", 18080)
    yield state
    server.close()
    await server.wait_closed()
