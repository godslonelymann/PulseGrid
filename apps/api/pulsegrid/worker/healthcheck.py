import asyncio
from datetime import timedelta

from sqlalchemy import select

from pulsegrid.storage.db import Session, now
from pulsegrid.storage.models import Heartbeat


async def check():
    async with Session() as s:
        alive = await s.scalar(select(Heartbeat.id).where(Heartbeat.seen_at > now() - timedelta(seconds=15)).limit(1))
        if not alive:
            raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(check())
