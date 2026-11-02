"""Reset only the dedicated verification database, then seed an explicit test owner."""

import asyncio

from pulsegrid.auth.security import hasher, redis
from pulsegrid.storage.db import Session, engine
from pulsegrid.storage.models import User
from sqlalchemy import text


async def run():
    if engine.url.database != "pulsegrid_test":
        raise RuntimeError("Refusing to reset a non-test database")
    async with Session.begin() as s:
        await s.execute(text("TRUNCATE users, worker_heartbeats CASCADE"))
        s.add(User(email="browser@example.com", password_hash=hasher.hash("browser-test-password-123")))
    await redis.flushdb()
    await redis.aclose()
    await engine.dispose()


asyncio.run(run())
