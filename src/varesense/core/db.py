import logging

from sqlalchemy import MetaData, pool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from varesense.core.config import settings

logger = logging.getLogger("varesense.db")

# NullPool: no connection reused across event loops (tests create several
# TestClient loops). `ponytail:` swap to QueuePool once single-loop/prod
# deployment is verified.
engine = create_async_engine(
    settings.database_url, echo=False, poolclass=pool.NullPool
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=convention)


async def get_session():
    async with SessionLocal() as session:
        yield session
