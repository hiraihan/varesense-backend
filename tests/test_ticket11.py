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


def _envelope(node, value=2.5):
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


def _post_rule(hdr):
    return client.post(
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


def _make_alert(hdr, node, cred):
    _post_rule(hdr)
    auth = _basic_hdr(cred["client_id"], cred["secret"])
    client.post("/v1/telemetry", headers=auth, json=_envelope(node, value=2.5))
    return client.get("/v1/alerts", headers=hdr).json()[0]


def _record_body(machine, alert_id=None):
    return {
        "asset_id": machine["id"],
        "alert_id": alert_id,
        "kind": "replacement",
        "findings": {"parts": ["bearing-6205"], "duration_h": 2},
        "outcome": "completed",
    }


def test_create_record_linked_to_alert_and_audited(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, cred, machine = _setup_device_and_asset(hdr)
    alert = _make_alert(hdr, node, cred)

    r = client.post(
        "/v1/maintenance/records", headers=hdr, json=_record_body(machine, alert["id"])
    )
    assert r.status_code == 201
    rec = r.json()
    assert rec["asset_id"] == machine["id"]
    assert rec["alert_id"] == alert["id"]
    assert rec["kind"] == "replacement"
    assert rec["outcome"] == "completed"
    assert rec["findings"]["parts"] == ["bearing-6205"]
    uuid.UUID(rec["author_user_id"])  # author recorded

    async def _audit():
        conn = await asyncpg.connect(_pg_url())
        try:
            n = await conn.fetchval(
                "SELECT count(*) FROM audit_log WHERE action='maintenance_record_create'"
            )
        finally:
            await conn.close()
        return n

    assert _run(_audit()) == 1


def test_list_filters_by_asset_and_alert(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, cred, machine = _setup_device_and_asset(hdr)
    alert = _make_alert(hdr, node, cred)

    client.post("/v1/maintenance/records", headers=hdr, json=_record_body(machine))
    client.post("/v1/maintenance/records", headers=hdr, json=_record_body(machine, alert["id"]))

    all_recs = client.get("/v1/maintenance/records", headers=hdr).json()
    assert len(all_recs) == 2

    by_asset = client.get(f"/v1/maintenance/records?asset_id={machine['id']}", headers=hdr).json()
    assert len(by_asset) == 2

    by_alert = client.get(f"/v1/maintenance/records?alert_id={alert['id']}", headers=hdr).json()
    assert len(by_alert) == 1
    assert by_alert[0]["alert_id"] == alert["id"]

    rec = client.get(f"/v1/maintenance/records/{all_recs[0]['id']}", headers=hdr).json()
    assert rec["id"] == all_recs[0]["id"]


def test_alert_must_match_asset(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, cred, machine = _setup_device_and_asset(hdr)
    alert = _make_alert(hdr, node, cred)

    site = client.post("/v1/assets", headers=hdr, json={"type": "site", "name": "S"}).json()
    body = _record_body(machine, alert["id"])
    body["asset_id"] = site["id"]
    assert (
        client.post("/v1/maintenance/records", headers=hdr, json=body).status_code == 400
    )


def test_validation_rejects_bad_kind_outcome(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    _, _, machine = _setup_device_and_asset(hdr)

    bad_kind = _record_body(machine)
    bad_kind["kind"] = "overhaul"
    assert (
        client.post("/v1/maintenance/records", headers=hdr, json=bad_kind).status_code == 422
    )

    bad_outcome = _record_body(machine)
    bad_outcome["outcome"] = "finished"
    assert (
        client.post("/v1/maintenance/records", headers=hdr, json=bad_outcome).status_code == 422
    )


def test_cross_tenant_blocked(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, cred, machine = _setup_device_and_asset(hdr)
    alert = _make_alert(hdr, node, cred)
    rec = client.post(
        "/v1/maintenance/records", headers=hdr, json=_record_body(machine, alert["id"])
    ).json()

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

    assert client.get(f"/v1/maintenance/records/{rec['id']}", headers=b_hdr).status_code == 404
    assert client.get("/v1/maintenance/records", headers=b_hdr).json() == []
