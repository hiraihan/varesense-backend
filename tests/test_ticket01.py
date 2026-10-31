import asyncio

import asyncpg
from alembic import command
from alembic.config import Config as AlembicConfig
from fastapi.testclient import TestClient

from varesense.core.config import settings
from varesense.main import app

client = TestClient(app)


def test_ready_reports_database():
    r = client.get("/v1/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"


async def test_key_tables_exist_after_migrations():
    url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    try:
        rows = await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' "
            "AND tablename IN ('users','organizations','assets')"
        )
    finally:
        await conn.close()
    assert {r["tablename"] for r in rows} == {"users", "organizations", "assets"}


def test_migrations_apply_to_fresh_db():
    sync_url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    sync_prefix = sync_url.rsplit("/", 1)[0]
    async_prefix = settings.database_url.rsplit("/", 1)[0]
    dbname = "varesense_test_mig"

    def _conn(db: str):
        return f"{sync_prefix}/{db}"

    async def _create_db():
        conn = await asyncpg.connect(_conn("postgres"))
        try:
            await conn.execute(f"DROP DATABASE IF EXISTS {dbname}")
            await conn.execute(f"CREATE DATABASE {dbname}")
        finally:
            await conn.close()

    asyncio.run(_create_db())
    try:
        cfg = AlembicConfig()
        cfg.set_main_option("script_location", "alembic")
        cfg.set_main_option("sqlalchemy.url", f"{async_prefix}/{dbname}")
        command.upgrade(cfg, "head")

        async def _check_tables():
            conn = await asyncpg.connect(_conn(dbname))
            try:
                rows = await conn.fetch(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                    "AND tablename IN ('users','organizations','assets')"
                )
            finally:
                await conn.close()
            return {r["tablename"] for r in rows}

        assert asyncio.run(_check_tables()) == {
            "users", "organizations", "assets",
        }
    finally:
        async def _drop_db():
            conn = await asyncpg.connect(_conn("postgres"))
            try:
                await conn.execute(f"DROP DATABASE IF EXISTS {dbname}")
            finally:
                await conn.close()

        asyncio.run(_drop_db())
