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
                "dead_letter, alerts, alert_rules, baselines, anomalies "
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


def _envelope(node, value=2.5, measured_at="2026-10-31T12:00:00Z", **over):
    data = {
        "event_id": str(uuid.uuid4()),
        "device_id": node["id"],
        "measured_at": measured_at,
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


def _ingest(auth, node, value, measured_at):
    client.post("/v1/telemetry", headers=auth, json=_envelope(node, value=value, measured_at=measured_at))


async def _count(table):
    conn = await asyncpg.connect(_pg_url())
    try:
        n = await conn.fetchval(f"SELECT count(*) FROM {table}")
    finally:
        await conn.close()
    return n


STABLE = [100.0, 100.1, 99.9, 100.0, 100.05, 99.95, 100.0, 100.02]
OUTLIERS = [105.0, 106.0, 107.0]


def _seed_telemetry(auth, node, values, start_hour):
    for i, v in enumerate(values):
        _ingest(auth, node, v, f"2026-10-31T{start_hour + i:02d}:00:00Z")


def test_baseline_formed_from_valid_telemetry(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, cred, machine = _setup_device_and_asset(hdr)
    auth = _basic_hdr(cred["client_id"], cred["secret"])
    _seed_telemetry(auth, node, STABLE, 0)

    r = client.post(
        "/v1/analytics/baselines",
        headers=hdr,
        json={
            "asset_id": machine["id"],
            "measurement_type": "vibration",
            "period_start": "2026-10-31T00:00:00Z",
            "period_end": "2026-10-31T23:59:59Z",
        },
    )
    assert r.status_code == 201
    bl = r.json()
    assert bl["n"] == len(STABLE)
    assert bl["active"] is True
    assert bl["method_version"] == "v1"
    assert 99 < bl["mean"] < 101
    assert bl["std"] > 0
    assert bl["training_until"]


def test_baseline_rejects_empty_period(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, cred, machine = _setup_device_and_asset(hdr)
    r = client.post(
        "/v1/analytics/baselines",
        headers=hdr,
        json={
            "asset_id": machine["id"],
            "measurement_type": "vibration",
            "period_start": "2026-10-31T00:00:00Z",
            "period_end": "2026-10-31T23:59:59Z",
        },
    )
    assert r.status_code == 400


def test_trend_query(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, cred, machine = _setup_device_and_asset(hdr)
    auth = _basic_hdr(cred["client_id"], cred["secret"])
    _seed_telemetry(auth, node, STABLE, 0)

    r = client.get(
        "/v1/analytics/trends",
        headers=hdr,
        params={
            "asset_id": machine["id"],
            "measurement_type": "vibration",
            "quality": "valid",
            "start": "2026-10-31T00:00:00Z",
            "end": "2026-10-31T23:59:59Z",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["count"] == len(STABLE)
    assert len(body["samples"]) == len(STABLE)
    s0 = body["samples"][0]
    assert s0["quality_status"] == "valid"
    assert s0["unit"] == "mm/s"
    assert s0["sensor_id"]


def test_anomaly_detection_flags_gradual(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, cred, machine = _setup_device_and_asset(hdr)
    auth = _basic_hdr(cred["client_id"], cred["secret"])
    _seed_telemetry(auth, node, STABLE, 0)

    bl = client.post(
        "/v1/analytics/baselines",
        headers=hdr,
        json={
            "asset_id": machine["id"],
            "measurement_type": "vibration",
            "period_start": "2026-10-31T00:00:00Z",
            "period_end": "2026-10-31T23:59:59Z",
        },
    ).json()

    _seed_telemetry(auth, node, OUTLIERS, 10)

    r = client.post("/v1/analytics/anomalies/run", headers=hdr, params={"z_threshold": 3.0})
    assert r.status_code == 200
    assert r.json()["created"] == len(OUTLIERS)

    anomalies = client.get("/v1/analytics/anomalies", headers=hdr).json()
    assert len(anomalies) == len(OUTLIERS)
    assert all(a["baseline_id"] == bl["id"] for a in anomalies)
    # gradual degradation flagged on >=3 consecutive same-direction deviations
    assert sum(1 for a in anomalies if a["gradual"]) >= 1
    assert anomalies[0]["source"]["method"] == "z-score"
    assert "mean" in anomalies[0]["evidence"]


def test_anomaly_generates_alert(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, cred, machine = _setup_device_and_asset(hdr)
    auth = _basic_hdr(cred["client_id"], cred["secret"])
    _seed_telemetry(auth, node, STABLE, 0)

    client.post(
        "/v1/analytics/baselines",
        headers=hdr,
        json={
            "asset_id": machine["id"],
            "measurement_type": "vibration",
            "period_start": "2026-10-31T00:00:00Z",
            "period_end": "2026-10-31T23:59:59Z",
        },
    )
    _seed_telemetry(auth, node, OUTLIERS, 10)

    r = client.post("/v1/analytics/anomalies/run", headers=hdr, params={"z_threshold": 3.0})
    assert r.status_code == 200

    alerts = client.get("/v1/alerts", headers=hdr).json()
    assert len(alerts) >= 1
    a = alerts[0]
    assert a["rule_id"] is None  # statistical anomaly: no threshold rule
    assert a["parameter"] == "vibration"
    assert a["severity"] == "warning"
    assert a["source"]["method"] == "z-score"


def test_anomaly_review_transition(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, cred, machine = _setup_device_and_asset(hdr)
    auth = _basic_hdr(cred["client_id"], cred["secret"])
    _seed_telemetry(auth, node, STABLE, 0)
    client.post(
        "/v1/analytics/baselines",
        headers=hdr,
        json={
            "asset_id": machine["id"],
            "measurement_type": "vibration",
            "period_start": "2026-10-31T00:00:00Z",
            "period_end": "2026-10-31T23:59:59Z",
        },
    )
    _seed_telemetry(auth, node, OUTLIERS, 10)
    client.post("/v1/analytics/anomalies/run", headers=hdr)

    anomaly_id = client.get("/v1/analytics/anomalies", headers=hdr).json()[0]["id"]
    r = client.patch(f"/v1/analytics/anomalies/{anomaly_id}", headers=hdr, json={"status": "reviewed"})
    assert r.status_code == 200
    assert r.json()["status"] == "reviewed"
    # reviewed is terminal: back to pending rejected
    assert client.patch(
        f"/v1/analytics/anomalies/{anomaly_id}", headers=hdr, json={"status": "pending"}
    ).status_code == 400


def test_anomaly_cross_tenant_blocked(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, cred, machine = _setup_device_and_asset(hdr)
    auth = _basic_hdr(cred["client_id"], cred["secret"])
    _seed_telemetry(auth, node, STABLE, 0)
    client.post(
        "/v1/analytics/baselines",
        headers=hdr,
        json={
            "asset_id": machine["id"],
            "measurement_type": "vibration",
            "period_start": "2026-10-31T00:00:00Z",
            "period_end": "2026-10-31T23:59:59Z",
        },
    )
    _seed_telemetry(auth, node, OUTLIERS, 10)
    client.post("/v1/analytics/anomalies/run", headers=hdr)
    anomaly_id = client.get("/v1/analytics/anomalies", headers=hdr).json()[0]["id"]

    async def _seed_org_b():
        conn = await asyncpg.connect(_pg_url())
        try:
            org_id = uuid.uuid4()
            await conn.execute("INSERT INTO organizations (id, name) VALUES ($1, $2)", org_id, "Beta")
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

    assert client.get("/v1/analytics/anomalies", headers=b_hdr).json() == []
    assert client.patch(
        f"/v1/analytics/anomalies/{anomaly_id}", headers=b_hdr, json={"status": "reviewed"}
    ).status_code == 404
