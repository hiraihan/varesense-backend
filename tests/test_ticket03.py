import asyncio

import asyncpg
import pytest
from fastapi.testclient import TestClient

from varesense.core.config import settings
from varesense.main import app

client = TestClient(app)


@pytest.fixture
def clean_db():
    async def _wipe():
        conn = await asyncpg.connect(_pg_url())
        try:
            await conn.execute(
                "TRUNCATE users, organizations, assets, audit_log "
                "RESTART IDENTITY CASCADE"
            )
        finally:
            await conn.close()

    _run(_wipe())
    yield
    _run(_wipe())


def _pg_url() -> str:
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://")


def _run(awaitable):
    return asyncio.run(awaitable)


async def _fetch_audit() -> list[dict]:
    conn = await asyncpg.connect(_pg_url())
    try:
        rows = await conn.fetch("SELECT * FROM audit_log ORDER BY created_at")
    finally:
        await conn.close()
    return [dict(r) for r in rows]


def _bootstrap():
    return client.post(
        "/v1/auth/bootstrap",
        json={"org_name": "Acme", "email": "admin@acme.io", "password": "s3cret"},
    ).json()


def _admin_hdr(boot):
    return {"Authorization": f"Bearer {boot['access_token']}"}


def test_login_writes_audit(clean_db):
    boot = _bootstrap()
    client.post("/v1/auth/login", json={"email": "admin@acme.io", "password": "s3cret"})
    rows = _run(_fetch_audit())
    actions = {r["action"] for r in rows}
    assert "login" in actions
    login_row = next(r for r in rows if r["action"] == "login")
    assert login_row["object_type"] == "user"
    assert login_row["actor_user_id"] is not None


def test_create_user_writes_audit(clean_db):
    boot = _bootstrap()
    client.post(
        "/v1/auth/users",
        headers=_admin_hdr(boot),
        json={"email": "op@acme.io", "password": "s3cret", "role": "operator"},
    )
    actions = {r["action"] for r in _run(_fetch_audit())}
    assert "create_user" in actions


def test_create_asset_writes_audit(clean_db):
    boot = _bootstrap()
    client.post(
        "/v1/assets", headers=_admin_hdr(boot), json={"type": "site", "name": "Plant A"}
    )
    rows = _run(_fetch_audit())
    assert any(r["action"] == "create_asset" and r["object_type"] == "asset" for r in rows)


def test_audit_append_only(clean_db):
    boot = _bootstrap()
    client.post("/v1/auth/login", json={"email": "admin@acme.io", "password": "s3cret"})
    assert _run(_fetch_audit())

    async def _try_update_and_delete():
        conn = await asyncpg.connect(_pg_url())
        try:
            with pytest.raises(Exception, match="append-only"):
                await conn.execute("UPDATE audit_log SET action='hacked'")
            with pytest.raises(Exception, match="append-only"):
                await conn.execute("DELETE FROM audit_log")
        finally:
            await conn.close()

    _run(_try_update_and_delete())


def test_ready_reflects_audit(clean_db):
    r = client.get("/v1/ready")
    assert r.status_code == 200
    assert r.json()["audit"] == "ok"


def test_audit_failure_not_silent(monkeypatch, clean_db):
    import varesense.assets.router as assets_router

    failing_client = TestClient(app, raise_server_exceptions=False)

    def boom(*args, **kwargs):
        raise RuntimeError("audit down")

    monkeypatch.setattr(assets_router, "record_audit", boom)
    boot = _bootstrap()
    r = failing_client.post(
        "/v1/assets", headers=_admin_hdr(boot), json={"type": "site", "name": "Plant A"}
    )
    assert r.status_code == 500
