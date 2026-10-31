import asyncio
import base64
import csv
import io
import uuid

import asyncpg
import pytest
from fastapi.testclient import TestClient

from varesense.core.config import settings
from varesense.core.security import hash_password
from varesense.main import app

client = TestClient(app)

PERIOD_START = "2026-10-31T00:00:00Z"
PERIOD_END = "2026-11-01T00:00:00Z"


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
    return node, cred, machine, site, area, line


def _envelope(node, value, quality="valid", event_id=None):
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "device_id": node["id"],
        "measured_at": "2026-10-31T12:00:00Z",
        "sequence_id": 1,
        "measurement_type": "vibration",
        "value": value,
        "unit": "mm/s",
        "quality_status": quality,
        "schema_version": "1.0",
        "configuration_version": "cfg-1",
        "firmware_version": "fw-2",
    }


def _seed_data(hdr, node, cred):
    auth = _basic_hdr(cred["client_id"], cred["secret"])
    assert client.post("/v1/telemetry", headers=auth, json=_envelope(node, 2.5)).status_code == 202
    # non-valid sample: valid schema, but quality=stale -> quality gap, excluded from sums
    assert client.post(
        "/v1/telemetry", headers=auth, json=_envelope(node, 3.0, quality="stale")
    ).status_code == 202


def _export(params, hdr):
    r = client.get("/v1/reports/export", headers=hdr, params=params)
    return r


def test_export_organization_scope_with_header_and_gaps(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, cred, machine, *_ = _setup(hdr)
    _seed_data(hdr, node, cred)

    r = _export(
        {"scope": "organization", "start": PERIOD_START, "end": PERIOD_END}, hdr
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]

    body = r.text
    assert "#,scope,organization" in body
    assert "#,method," in body
    assert "#,assumptions," in body
    assert "#,quality_gap,M1,stale,1" in body
    assert "#,coverage,Plant,0" in body
    assert "#,coverage,M1,1" in body

    rows = list(csv.reader(io.StringIO(body)))
    data_rows = [row for row in rows if row and not row[0].startswith("#") and row[0] != "asset_name"]
    assert any(
        row[0] == "M1" and row[1] == "machine" and row[4] == "1"
        and row[5] == "2.5" and row[6] == "mm/s"
        for row in data_rows
    )

    async def _audit():
        conn = await asyncpg.connect(_pg_url())
        try:
            n = await conn.fetchval(
                "SELECT count(*) FROM audit_log WHERE action='report_export'"
            )
        finally:
            await conn.close()
        return n

    assert _run(_audit()) == 1


def test_export_asset_scope_limits_rows(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, cred, machine, *_ = _setup(hdr)
    _seed_data(hdr, node, cred)

    # empty period -> no data rows but header present
    r = _export(
        {
            "scope": "asset",
            "asset_id": machine["id"],
            "start": "2026-11-10T00:00:00Z",
            "end": "2026-11-11T00:00:00Z",
        },
        hdr,
    )
    assert r.status_code == 200
    rows = [row for row in csv.reader(io.StringIO(r.text)) if row and row[0] == "asset_name"]
    assert len(rows) == 1  # header only, zero data rows


def test_export_invalid_scope_rejected(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    r = _export(
        {"scope": "region", "start": PERIOD_START, "end": PERIOD_END}, hdr
    )
    assert r.status_code == 422


def test_export_cross_tenant_blocked(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, cred, machine, *_ = _setup(hdr)
    _seed_data(hdr, node, cred)

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

    # access scope: asset belongs to org A -> 404 for org B
    r = _export(
        {
            "scope": "asset",
            "asset_id": machine["id"],
            "start": PERIOD_START,
            "end": PERIOD_END,
        },
        b_hdr,
    )
    assert r.status_code == 404

    # org scope: empty (no assets) but 200
    r2 = _export(
        {"scope": "organization", "start": PERIOD_START, "end": PERIOD_END}, b_hdr
    )
    assert r2.status_code == 200
    assert "#,assets_included,0" in r2.text


def test_export_pdf_format(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node, cred, machine, *_ = _setup(hdr)
    _seed_data(hdr, node, cred)

    r = _export(
        {
            "scope": "organization",
            "start": PERIOD_START,
            "end": PERIOD_END,
            "format": "pdf",
        },
        hdr,
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert "attachment" in r.headers["content-disposition"]
    assert r.content[:4] == b"%PDF"


def test_export_invalid_format_rejected(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    r = _export(
        {
            "scope": "organization",
            "start": PERIOD_START,
            "end": PERIOD_END,
            "format": "xlsx",
        },
        hdr,
    )
    assert r.status_code == 422
