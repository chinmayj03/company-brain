"""Async database connection pool shared across the AI service."""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from companybrain.config import settings
import structlog

log = structlog.get_logger(__name__)
_engine = None
_session_factory = None


async def init_db_pool():
    global _engine, _session_factory
    _engine = create_async_engine(settings.database_url, pool_size=10, max_overflow=5, echo=False, pool_pre_ping=True)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    log.info("DB pool initialised", url=settings.database_url.split("@")[-1])


async def close_db_pool():
    if _engine:
        await _engine.dispose()


def get_session() -> AsyncSession:
    return _session_factory()
