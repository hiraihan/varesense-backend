import asyncio

import asyncpg
import pytest
from fastapi.testclient import TestClient

from varesense.core.config import settings
from varesense.main import app

client = TestClient(app)


def _pg_url() -> str:
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://")


def _run(awaitable):
    return asyncio.run(awaitable)


@pytest.fixture
def clean_db():
    async def _wipe():
        conn = await asyncpg.connect(_pg_url())
        try:
            await conn.execute(
                "TRUNCATE users, organizations, assets, asset_history, audit_log, "
                "edge_nodes, device_credentials, device_endpoints, telemetry, "
                "dead_letter, alerts, alert_rules, maintenance_records, "
                "refresh_tokens RESTART IDENTITY CASCADE"
            )
        finally:
            await conn.close()

    _run(_wipe())
    yield
    _run(_wipe())


def _bootstrap():
    return client.post(
        "/v1/auth/bootstrap",
        json={"org_name": "Acme", "email": "admin@acme.io", "password": "s3cret"},
    ).json()


def test_refresh_rotation_revokes_old_token(clean_db):
    boot = _bootstrap()
    old_refresh = boot["refresh_token"]

    r1 = client.post("/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert r1.status_code == 200
    new_refresh = r1.json()["refresh_token"]
    assert new_refresh != old_refresh

    # replaying the old token must fail (it was revoked by the rotation)
    r2 = client.post("/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert r2.status_code == 401

    # the new token still works
    r3 = client.post("/v1/auth/refresh", json={"refresh_token": new_refresh})
    assert r3.status_code == 200


def test_refresh_rejects_unknown_token(clean_db):
    _bootstrap()
    r = client.post("/v1/auth/refresh", json={"refresh_token": "not-a-real-token"})
    assert r.status_code == 401
