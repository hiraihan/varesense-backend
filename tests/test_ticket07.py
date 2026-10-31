import asyncio
import base64
import uuid

import asyncpg
import pytest
from fastapi.testclient import TestClient

from varesense.core.config import settings
from varesense.core.security import hash_password
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
                "dead_letter, alerts, alert_rules "
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


def _setup_device_and_asset(hdr):
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
    return node, cred, machine


def _envelope(node, value=2.5, **over):
    data = {
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
    data.update(over)
    return data


def _post_rule(hdr, **over):
    data = {
        "name": "vibration high",
        "parameter": "vibration",
        "operator": "gt",
        "value": 2.0,
        "unit": "mm/s",
        "severity": "warning",
    }
    data.update(over)
    return client.post("/v1/alerts/rules", headers=hdr, json=data)


async def _count(table):
    conn = await asyncpg.connect(_pg_url())
    try:
        n = await conn.fetchval(f"SELECT count(*) FROM {table}")
    finally:
        await conn.close()
    return n


def test_rule_crud_versioned(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)

    r = _post_rule(hdr)
    assert r.status_code == 201
    v1 = r.json()
    assert v1["version"] == 1
    assert v1["active"] is True
    key = v1["rule_key"]

    r2 = client.patch(f"/v1/alerts/rules/{key}", headers=hdr, json={"value": 3.0})
    assert r2.status_code == 200
    v2 = r2.json()
    assert v2["version"] == 2
    assert v2["active"] is True
    assert v2["value"] == 3.0

    # v1 deactivated, v2 active
    lst = client.get("/v1/alerts/rules", headers=hdr).json()
    assert [x["version"] for x in lst] == [2]

    hist = client.get(f"/v1/alerts/rules/{key}", headers=hdr).json()
    assert {h["version"] for h in hist} == {1, 2}

    # deactivate (delete) rule -> no active rules, history preserved
    assert client.delete(f"/v1/alerts/rules/{key}", headers=hdr).status_code == 204
    assert client.get("/v1/alerts/rules", headers=hdr).json() == []
    hist = client.get(f"/v1/alerts/rules/{key}", headers=hdr).json()
    assert {h["version"] for h in hist} == {1, 2}


def test_on_ingest_creates_alert(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, cred, machine = _setup_device_and_asset(hdr)
    _post_rule(hdr)

    auth = _basic_hdr(cred["client_id"], cred["secret"])
    assert client.post("/v1/telemetry", headers=auth, json=_envelope(node, value=2.5)).status_code == 202

    alerts = client.get("/v1/alerts", headers=hdr).json()
    assert len(alerts) == 1
    a = alerts[0]
    assert a["status"] == "detected"
    assert a["severity"] == "warning"
    assert a["asset_id"] == machine["id"]
    assert a["parameter"] == "vibration"
    assert a["value"] == 2.5
    assert a["evidence"]["threshold"] == 2.0
    assert a["source"]["firmware_version"] == "fw-2"


def test_threshold_not_crossed_no_alert(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, cred, _ = _setup_device_and_asset(hdr)
    _post_rule(hdr)

    auth = _basic_hdr(cred["client_id"], cred["secret"])
    client.post("/v1/telemetry", headers=auth, json=_envelope(node, value=1.0))
    assert _run(_count("alerts")) == 0


def _walk_chain(hdr, alert_id, target):
    for s in ("open", "acknowledged", "assigned_investigating", "action_recorded", "resolved", "closed"):
        client.post(f"/v1/alerts/{alert_id}/transition", headers=hdr, json={"status": s})
        if s == target:
            break


def test_dedup_preserves_until_terminal(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, cred, _ = _setup_device_and_asset(hdr)
    _post_rule(hdr)

    auth = _basic_hdr(cred["client_id"], cred["secret"])
    client.post("/v1/telemetry", headers=auth, json=_envelope(node, value=2.5))
    client.post("/v1/telemetry", headers=auth, json=_envelope(node, value=3.5))
    assert _run(_count("alerts")) == 1
    # telemetry events preserved regardless
    assert _run(_count("telemetry")) == 2

    alert_id = client.get("/v1/alerts", headers=hdr).json()[0]["id"]
    # resolved is terminal: a new occurrence raises a new alert
    _walk_chain(hdr, alert_id, "resolved")
    client.post("/v1/telemetry", headers=auth, json=_envelope(node, value=4.0))
    assert _run(_count("alerts")) == 2

    second_id = client.get("/v1/alerts?status=detected", headers=hdr).json()[0]["id"]
    _walk_chain(hdr, second_id, "closed")
    client.post("/v1/telemetry", headers=auth, json=_envelope(node, value=5.0))
    assert _run(_count("alerts")) == 3


def test_transitions_validated_and_audited(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, cred, _ = _setup_device_and_asset(hdr)
    _post_rule(hdr)
    auth = _basic_hdr(cred["client_id"], cred["secret"])
    client.post("/v1/telemetry", headers=auth, json=_envelope(node, value=2.5))

    alert_id = client.get("/v1/alerts", headers=hdr).json()[0]["id"]
    # illegal jump rejected
    assert client.post(
        f"/v1/alerts/{alert_id}/transition", headers=hdr, json={"status": "closed"}
    ).status_code == 400
    # happy path detected -> open -> acknowledged -> assigned -> action -> resolved -> closed
    for s in ("open", "acknowledged", "assigned_investigating", "action_recorded", "resolved", "closed"):
        r = client.post(f"/v1/alerts/{alert_id}/transition", headers=hdr, json={"status": s})
        assert r.status_code == 200, s
        assert r.json()["status"] == s
    # terminal: further transition rejected
    assert client.post(
        f"/v1/alerts/{alert_id}/transition", headers=hdr, json={"status": "open"}
    ).status_code == 400

    async def _audit_transitions():
        conn = await asyncpg.connect(_pg_url())
        try:
            rows = await conn.fetch(
                "SELECT action, details FROM audit_log WHERE action='alert_transition'"
            )
        finally:
            await conn.close()
        return [dict(r) for r in rows]

    rows = _run(_audit_transitions())
    assert len(rows) == 6


def test_alert_list_filters(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, cred, machine = _setup_device_and_asset(hdr)
    _post_rule(hdr)
    auth = _basic_hdr(cred["client_id"], cred["secret"])
    client.post("/v1/telemetry", headers=auth, json=_envelope(node, value=2.5))

    assert len(client.get("/v1/alerts?status=detected", headers=hdr).json()) == 1
    assert len(client.get("/v1/alerts?severity=warning", headers=hdr).json()) == 1
    assert len(client.get("/v1/alerts?status=closed", headers=hdr).json()) == 0
    assert len(client.get(f"/v1/alerts?asset_id={machine['id']}", headers=hdr).json()) == 1


def test_cross_tenant_alert_blocked(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, cred, _ = _setup_device_and_asset(hdr)
    _post_rule(hdr)
    auth = _basic_hdr(cred["client_id"], cred["secret"])
    client.post("/v1/telemetry", headers=auth, json=_envelope(node, value=2.5))
    alert_id = client.get("/v1/alerts", headers=hdr).json()[0]["id"]

    async def _seed_org_b():
        conn = await asyncpg.connect(_pg_url())
        try:
            org_id = uuid.uuid4()
            await conn.execute(
                "INSERT INTO organizations (id, name) VALUES ($1, $2)", org_id, "Beta"
            )
            await conn.execute(
                "INSERT INTO users (id, organization_id, email, hashed_password, role) "
                "VALUES ($1, $2, $3, $4, 'operator')",
                uuid.uuid4(),
                org_id,
                "op@beta.io",
                hash_password("s3cret"),
            )
        finally:
            await conn.close()

    _run(_seed_org_b())
    b_login = client.post("/v1/auth/login", json={"email": "op@beta.io", "password": "s3cret"})
    b_hdr = {"Authorization": f"Bearer {b_login.json()['access_token']}"}

    assert client.get(f"/v1/alerts/{alert_id}", headers=b_hdr).status_code == 404
    assert client.post(
        f"/v1/alerts/{alert_id}/transition", headers=b_hdr, json={"status": "open"}
    ).status_code == 404
