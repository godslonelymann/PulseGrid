from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from pulsegrid.config import settings


class Base(DeclarativeBase):
    pass


def now():
    return datetime.now(UTC)


engine = create_async_engine(
    settings().database_url,
    pool_size=5,
    max_overflow=5,
    pool_timeout=5,
    pool_pre_ping=True,
    connect_args={"connect_timeout": 3, "options": "-c statement_timeout=10000 -c lock_timeout=5000"},
)
Session = async_sessionmaker(engine, expire_on_commit=False)


async def db():
    async with Session() as session:
        yield session
