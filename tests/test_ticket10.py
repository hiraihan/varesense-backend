import asyncio
import base64
import uuid

import asyncpg
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from varesense.core.config import settings
from varesense.main import app
from varesense.realtime.hub import QUEUE_MAXSIZE, hub

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
                "dead_letter, alerts, alert_rules, baselines, anomalies, carbon_factors "
                "RESTART IDENTITY CASCADE"
            )
        finally:
            await conn.close()

    _run(_wipe())
    hub.clear()
    yield
    hub.clear()
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


def _setup(hdr):
    node = client.post("/v1/devices", headers=hdr, json={"name": "N"}).json()
    cred = client.post(f"/v1/devices/{node['id']}/credentials", headers=hdr, json={}).json()
    site = client.post("/v1/assets", headers=hdr, json={"type": "site", "name": "Plant"}).json()
    area = client.post(
        "/v1/assets", headers=hdr, json={"type": "area", "name": "Zone", "parent_id": site["id"]}
    ).json()
    line = client.post(
        "/v1/assets",
        headers=hdr,
        json={"type": "production_line", "name": "L1", "parent_id": area["id"]},
    ).json()
    machine = client.post(
        "/v1/assets",
        headers=hdr,
        json={"type": "machine", "name": "M1", "parent_id": line["id"]},
    ).json()
    client.post(
        f"/v1/devices/{node['id']}/endpoints",
        headers=hdr,
        json={"kind": "sensor", "name": "Vibe", "asset_id": machine["id"]},
    )
    client.post(
        "/v1/alerts/rules",
        headers=hdr,
        json={
            "name": "vibration high",
            "parameter": "vibration",
            "operator": "gt",
            "value": 2.0,
            "unit": "mm/s",
            "severity": "warning",
        },
    )
    return node, cred, machine


def _envelope(node, value):
    return {
        "event_id": str(uuid.uuid4()),
        "device_id": node["id"],
        "measured_at": "2026-10-31T12:00:00Z",
        "sequence_id": 1,
        "measurement_type": "vibration",
        "value": value,
        "unit": "mm/s",
        "quality_status": "valid",
        "schema_version": "1.0",
        "configuration_version": "cfg-1",
        "firmware_version": "fw-2",
    }


def test_ws_pushes_new_alert(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, cred, _ = _setup(hdr)
    auth = _basic_hdr(cred["client_id"], cred["secret"])

    with client.websocket_connect(f"/v1/ws?token={boot['access_token']}") as ws:
        assert client.post(
            "/v1/telemetry", headers=auth, json=_envelope(node, 2.5)
        ).status_code == 202
        msg = ws.receive_json()
        assert msg["type"] == "alert"
        assert msg["severity"] == "warning"
        assert msg["status"] == "detected"


def test_ws_pushes_device_status_change(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, _, _ = _setup(hdr)

    with client.websocket_connect(f"/v1/ws?token={boot['access_token']}") as ws:
        r = client.patch(
            f"/v1/devices/{node['id']}",
            headers=hdr,
            json={"device_status": "degraded"},
        )
        assert r.status_code == 200
        msg = ws.receive_json()
        assert msg["type"] == "device_status"
        assert msg["device_id"] == node["id"]
        assert msg["device_status"] == "degraded"


def test_hub_scope_out_of_scope_not_delivered(clean_db):
    org_a = uuid.uuid4()
    org_b = uuid.uuid4()
    qa = hub.subscribe(org_a)
    qb = hub.subscribe(org_b)

    hub.publish(org_a, {"type": "alert", "id": "1"})
    assert qa.qsize() == 1
    assert qb.qsize() == 0

    hub.unsubscribe(org_a, qa)
    hub.unsubscribe(org_b, qb)


def test_hub_delivery_failure_does_not_corrupt(clean_db):
    org = uuid.uuid4()
    q = hub.subscribe(org)
    for _ in range(QUEUE_MAXSIZE):
        hub.publish(org, {"n": 0})
    assert q.qsize() == QUEUE_MAXSIZE
    # queue is full; publish must not raise into the caller
    hub.publish(org, {"n": 1})
    assert q.qsize() == QUEUE_MAXSIZE
    hub.unsubscribe(org, q)


def test_ws_rejects_bad_token(clean_db):
    with pytest.raises(Exception):
        with client.websocket_connect("/v1/ws?token=not-a-token") as ws:
            ws.receive_json()


def test_ws_connection_lifecycle_cleanup(clean_db):
    boot = _bootstrap()
    assert not hub._orgs
    with client.websocket_connect(f"/v1/ws?token={boot['access_token']}") as ws:
        assert len(hub._orgs) == 1
    # after disconnect, the subscription is removed
    assert not hub._orgs
