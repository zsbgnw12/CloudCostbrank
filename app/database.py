"""SQLAlchemy async engine and session factory."""

import ssl

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


def _effective_database_ssl() -> bool:
    if settings.DATABASE_SSL is not None:
        return settings.DATABASE_SSL
    u = settings.DATABASE_URL.lower()
    if "localhost" in u or "127.0.0.1" in u:
        return False
    return True


# 显式控制 TLS，避免本地库误走 SSL 或云库参数与 asyncpg 行为不一致
_asyncpg_connect_args: dict = {}
if _effective_database_ssl():
    _asyncpg_connect_args["ssl"] = ssl.create_default_context()
else:
    _asyncpg_connect_args["ssl"] = False

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    connect_args=_asyncpg_connect_args,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=1800,
)

async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    """FastAPI dependency that yields an async DB session.

    Convention: get_db auto-commits after the route handler returns.
    Routes MAY call db.commit() themselves (the second commit is a harmless
    no-op), but it is not required — get_db guarantees the final commit.
    On any exception get_db rolls back automatically.
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
