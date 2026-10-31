import asyncio
import base64
import uuid
from datetime import datetime, timedelta, timezone

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
                "dead_letter, alerts, alert_rules, maintenance_records "
                "RESTART IDENTITY CASCADE"
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


def _admin_hdr(boot):
    return {"Authorization": f"Bearer {boot['access_token']}"}


def _basic_hdr(client_id, secret):
    tok = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
    return {"Authorization": f"Basic {tok}"}


def _setup_device_and_cred(hdr):
    node = client.post("/v1/devices", headers=hdr, json={"name": "N"}).json()
    cred = client.post(f"/v1/devices/{node['id']}/credentials", headers=hdr, json={}).json()
    return node, cred


def test_expired_credential_rejected(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, cred = _setup_device_and_cred(hdr)

    async def _expire():
        conn = await asyncpg.connect(_pg_url())
        try:
            await conn.execute(
                "UPDATE device_credentials SET expires_at = now() - interval '1 hour' "
                "WHERE client_id = $1",
                cred["client_id"],
            )
        finally:
            await conn.close()

    _run(_expire())

    auth = _basic_hdr(cred["client_id"], cred["secret"])
    r = client.post(
        "/v1/telemetry",
        headers=auth,
        json={"event_id": "e", "device_id": node["id"]},
    )
    assert r.status_code == 401


def test_issue_rejects_past_expiry(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, _ = _setup_device_and_cred(hdr)
    r = client.post(
        f"/v1/devices/{node['id']}/credentials",
        headers=hdr,
        json={"expires_at": "2020-01-01T00:00:00Z"},
    )
    assert r.status_code == 422


def test_rotate_preserves_expiry(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, cred = _setup_device_and_cred(hdr)

    target = datetime.now(timezone.utc) + timedelta(days=30)

    async def _set_expiry():
        conn = await asyncpg.connect(_pg_url())
        try:
            await conn.execute(
                "UPDATE device_credentials SET expires_at = $1 WHERE client_id = $2",
                target,
                cred["client_id"],
            )
        finally:
            await conn.close()

    _run(_set_expiry())

    r = client.post(
        f"/v1/devices/{node['id']}/credentials/{cred['id']}/rotate",
        headers=hdr,
    )
    assert r.status_code == 200
    new_expiry = datetime.fromisoformat(r.json()["expires_at"])
    assert abs((new_expiry - target).total_seconds()) < 60
