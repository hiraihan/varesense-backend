import asyncio
import base64
import json
import uuid

import asyncpg
import pytest
from fastapi.testclient import TestClient

from varesense.core.config import settings
from varesense.core.db import SessionLocal
from varesense.devices.models import EdgeNode
from varesense.main import app
from varesense.telemetry.subscriber import handle_mqtt_message

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
                "edge_nodes, device_credentials, device_endpoints, telemetry, dead_letter "
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


def _issue_device(hdr):
    node = client.post("/v1/devices", headers=hdr, json={"code": "edge-1", "name": "N"}).json()
    cred = client.post(f"/v1/devices/{node['id']}/credentials", headers=hdr, json={}).json()
    return node, cred


def _envelope(node, **over):
    data = {
        "event_id": str(uuid.uuid4()),
        "device_id": node["id"],
        "measured_at": "2026-10-31T12:00:00Z",
        "sequence_id": 1,
        "measurement_type": "vibration",
        "value": 2.5,
        "unit": "mm/s",
        "quality_status": "valid",
        "schema_version": "1.0",
        "configuration_version": "cfg-1",
        "firmware_version": "fw-2",
    }
    data.update(over)
    return data


async def _count(table):
    conn = await asyncpg.connect(_pg_url())
    try:
        n = await conn.fetchval(f"SELECT count(*) FROM {table}")
    finally:
        await conn.close()
    return n


def test_valid_ingest_and_idempotency(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, cred = _issue_device(hdr)
    auth = _basic_hdr(cred["client_id"], cred["secret"])

    env = _envelope(node)
    r = client.post("/v1/telemetry", headers=auth, json=env)
    assert r.status_code == 202
    assert _run(_count("telemetry")) == 1

    # duplicate event -> no double insert
    assert client.post("/v1/telemetry", headers=auth, json=env).status_code == 202
    assert _run(_count("telemetry")) == 1


def test_invalid_payload_dead_lettered(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, cred = _issue_device(hdr)
    auth = _basic_hdr(cred["client_id"], cred["secret"])

    bad = _envelope(node, quality_status="not-a-status")
    r = client.post("/v1/telemetry", headers=auth, json=bad)
    assert r.status_code == 400
    assert _run(_count("telemetry")) == 0
    assert _run(_count("dead_letter")) == 1


def test_device_mismatch_dead_lettered(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, cred = _issue_device(hdr)
    auth = _basic_hdr(cred["client_id"], cred["secret"])

    other = uuid.uuid4()
    r = client.post("/v1/telemetry", headers=auth, json=_envelope(node, device_id=str(other)))
    assert r.status_code == 400
    assert _run(_count("dead_letter")) == 1


def test_device_auth_rejected(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, cred = _issue_device(hdr)

    # wrong secret
    assert client.post(
        "/v1/telemetry", headers=_basic_hdr(cred["client_id"], "wrong"),
        json=_envelope(node),
    ).status_code == 401

    # revoked credential rejected
    client.post(f"/v1/devices/{node['id']}/credentials/{cred['id']}/revoke", headers=hdr)
    assert client.post(
        "/v1/telemetry", headers=_basic_hdr(cred["client_id"], cred["secret"]),
        json=_envelope(node),
    ).status_code == 401


def test_dead_letter_observability(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, cred = _issue_device(hdr)
    auth = _basic_hdr(cred["client_id"], cred["secret"])

    client.post("/v1/telemetry", headers=auth, json=_envelope(node, quality_status="nope"))

    r = client.get("/v1/telemetry/dead-letters", headers=hdr)
    assert r.status_code == 200
    dl = r.json()
    assert len(dl) == 1
    assert dl[0]["reason"] == "validation_failed"


def test_subscriber_ingests_and_dead_letters(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, cred = _issue_device(hdr)

    async def _run_sub():
        async with SessionLocal() as session:
            dbnode = await session.get(EdgeNode, uuid.UUID(node["id"]))
            ok = await handle_mqtt_message(
                session, dbnode, json.dumps(_envelope(node)).encode()
            )
            bad = await handle_mqtt_message(session, dbnode, b"not-json")
            await session.commit()
        return ok, bad

    ok, bad = _run(_run_sub())
    assert ok == "accepted"
    assert bad.startswith("dead-lettered")
    assert _run(_count("telemetry")) == 1
    assert _run(_count("dead_letter")) == 1
