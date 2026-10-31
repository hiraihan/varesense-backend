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
                "dead_letter, alerts, alert_rules, baselines, anomalies, carbon_factors "
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
    # sensor endpoints on machine and on area (different type -> context flag)
    client.post(
        f"/v1/devices/{node['id']}/endpoints",
        headers=hdr,
        json={"kind": "sensor", "name": "Vibe", "asset_id": machine["id"]},
    )
    client.post(
        f"/v1/devices/{node['id']}/endpoints",
        headers=hdr,
        json={"kind": "sensor", "name": "ZoneS", "asset_id": area["id"]},
    )
    return node, cred, machine, area


def _envelope(node, measurement_type, value, measured_at):
    return {
        "event_id": str(uuid.uuid4()),
        "device_id": node["id"],
        "measured_at": measured_at,
        "sequence_id": 1,
        "measurement_type": measurement_type,
        "value": value,
        "unit": "kWh" if measurement_type == "energy_kwh" else "units",
        "quality_status": "valid",
        "schema_version": "1.0",
        "configuration_version": "cfg-1",
        "firmware_version": "fw-2",
    }


def _ingest(auth, node, measurement_type, value, hour):
    client.post(
        "/v1/telemetry",
        headers=auth,
        json=_envelope(node, measurement_type, value, f"2026-10-31T{hour:02d}:00:00Z"),
    )


def _seed_energy(auth, node):
    for i, v in enumerate([10.0, 20.0, 30.0]):
        _ingest(auth, node, "energy_kwh", v, i)


def test_rollup_aggregates_on_read(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, cred, machine, _ = _setup(hdr)
    auth = _basic_hdr(cred["client_id"], cred["secret"])
    _seed_energy(auth, node)

    r = client.get(
        "/v1/energy/rollup",
        headers=hdr,
        params={
            "asset_id": machine["id"],
            "measurement_type": "energy_kwh",
            "granularity": "day",
            "agg": "sum",
            "start": "2026-10-31T00:00:00Z",
            "end": "2026-10-31T23:59:59Z",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["origin"] == "calculated"
    assert len(body["buckets"]) == 1
    bucket = body["buckets"][0]
    assert bucket["value"] == 60.0
    assert bucket["sample_count"] == 3
    assert bucket["origin"] == "calculated"
    assert bucket["delta"] is None

    r = client.get(
        "/v1/energy/rollup",
        headers=hdr,
        params={
            "asset_id": machine["id"],
            "measurement_type": "energy_kwh",
            "granularity": "hour",
            "agg": "sum",
            "start": "2026-10-31T00:00:00Z",
            "end": "2026-10-31T23:59:59Z",
        },
    )
    assert [b["value"] for b in r.json()["buckets"]] == [10.0, 20.0, 30.0]
    assert [b["delta"] for b in r.json()["buckets"]] == [None, 10.0, 10.0]


def test_rollup_invalid_agg_rejected(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, cred, machine, _ = _setup(hdr)
    auth = _basic_hdr(cred["client_id"], cred["secret"])
    _seed_energy(auth, node)
    r = client.get(
        "/v1/energy/rollup",
        headers=hdr,
        params={
            "asset_id": machine["id"],
            "measurement_type": "energy_kwh",
            "granularity": "day",
            "agg": "bogus",
            "start": "2026-10-31T00:00:00Z",
            "end": "2026-10-31T23:59:59Z",
        },
    )
    assert r.status_code == 422


def test_trend_origin_measured(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, cred, machine, _ = _setup(hdr)
    auth = _basic_hdr(cred["client_id"], cred["secret"])
    _seed_energy(auth, node)

    r = client.get(
        "/v1/energy/trend",
        headers=hdr,
        params={
            "asset_id": machine["id"],
            "measurement_type": "energy_kwh",
            "start": "2026-10-31T00:00:00Z",
            "end": "2026-10-31T23:59:59Z",
        },
    )
    body = r.json()
    assert body["origin"] == "measured"
    assert len(body["points"]) == 3
    assert body["points"][0]["origin"] == "measured"

    # aggregation selectable on trend too
    r = client.get(
        "/v1/energy/trend",
        headers=hdr,
        params={
            "asset_id": machine["id"],
            "measurement_type": "energy_kwh",
            "agg": "sum",
            "granularity": "day",
            "start": "2026-10-31T00:00:00Z",
            "end": "2026-10-31T23:59:59Z",
        },
    )
    agg_body = r.json()
    assert agg_body["origin"] == "calculated"
    assert len(agg_body["buckets"]) == 1
    assert agg_body["buckets"][0]["value"] == 60.0


def test_compare_flags_context_differences(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, cred, machine, area = _setup(hdr)
    auth = _basic_hdr(cred["client_id"], cred["secret"])
    _seed_energy(auth, node)

    r = client.post(
        "/v1/energy/compare",
        headers=hdr,
        json={
            "asset_id_a": machine["id"],
            "asset_id_b": area["id"],
            "measurement_type": "energy_kwh",
            "start": "2026-10-31T00:00:00Z",
            "end": "2026-10-31T23:59:59Z",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["basis"] == "time"
    assert "asset_types_differ" in body["flags"]
    assert len(body["assets"]) == 2

    # with a missing output denominator, comparison cannot normalize to output
    r = client.post(
        "/v1/energy/compare",
        headers=hdr,
        json={
            "asset_id_a": machine["id"],
            "asset_id_b": area["id"],
            "measurement_type": "energy_kwh",
            "output_measurement_type": "nonexistent",
            "start": "2026-10-31T00:00:00Z",
            "end": "2026-10-31T23:59:59Z",
        },
    )
    assert "output_denominator_missing" in r.json()["flags"]


def test_carbon_estimate_with_provenance(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, cred, machine, _ = _setup(hdr)
    auth = _basic_hdr(cred["client_id"], cred["secret"])
    _seed_energy(auth, node)

    factor = client.post(
        "/v1/energy/carbon-factors",
        headers=hdr,
        json={
            "name": "EPA grid",
            "unit": "kWh",
            "factor": 0.4,
            "source": "EPA eGRID",
            "version": "2024.1",
            "basis": "US average grid mix",
            "assumptions": {"year": 2024},
        },
    ).json()

    r = client.get(
        "/v1/energy/carbon",
        headers=hdr,
        params={
            "asset_id": machine["id"],
            "measurement_type": "energy_kwh",
            "factor_id": factor["id"],
            "start": "2026-10-31T00:00:00Z",
            "end": "2026-10-31T23:59:59Z",
        },
    )
    body = r.json()
    assert body["origin"] == "estimated"
    assert body["estimate"] == 24.0  # 60 kWh * 0.4
    assert body["factor"]["source"] == "EPA eGRID"
    assert body["factor"]["basis"] == "US average grid mix"


def test_efficiency_requires_output_denominator(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, cred, machine, _ = _setup(hdr)
    auth = _basic_hdr(cred["client_id"], cred["secret"])
    _seed_energy(auth, node)
    for i, v in enumerate([5.0, 5.0]):
        _ingest(auth, node, "output_units", v, i)

    r = client.get(
        "/v1/energy/efficiency",
        headers=hdr,
        params={
            "asset_id": machine["id"],
            "input_measurement_type": "energy_kwh",
            "output_measurement_type": "output_units",
            "start": "2026-10-31T00:00:00Z",
            "end": "2026-10-31T23:59:59Z",
        },
    )
    body = r.json()
    assert body["origin"] == "calculated"
    assert body["input_total"] == 60.0
    assert body["output_total"] == 10.0
    assert body["intensity"] == pytest.approx(6.0)
    assert body["efficiency"] == pytest.approx(10.0 / 60.0)

    # no valid output denominator -> consumption/trend only, no fabricated efficiency
    r = client.get(
        "/v1/energy/efficiency",
        headers=hdr,
        params={
            "asset_id": machine["id"],
            "input_measurement_type": "energy_kwh",
            "output_measurement_type": "nonexistent",
            "start": "2026-10-31T00:00:00Z",
            "end": "2026-10-31T23:59:59Z",
        },
    )
    body = r.json()
    assert body["intensity"] is None
    assert body["efficiency"] is None
    assert body["origin"] == "measured"
    assert "no valid output denominator" in body["basis_note"]


def test_carbon_cross_tenant_blocked(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, cred, machine, _ = _setup(hdr)
    auth = _basic_hdr(cred["client_id"], cred["secret"])
    _seed_energy(auth, node)
    factor = client.post(
        "/v1/energy/carbon-factors",
        headers=hdr,
        json={
            "name": "EPA grid",
            "unit": "kWh",
            "factor": 0.4,
            "source": "EPA eGRID",
            "version": "2024.1",
            "basis": "US average grid mix",
        },
    ).json()

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

    assert client.get("/v1/energy/carbon-factors", headers=b_hdr).json() == []
    assert client.get(
        "/v1/energy/carbon",
        headers=b_hdr,
        params={
            "asset_id": machine["id"],
            "measurement_type": "energy_kwh",
            "factor_id": factor["id"],
            "start": "2026-10-31T00:00:00Z",
            "end": "2026-10-31T23:59:59Z",
        },
    ).status_code == 404
