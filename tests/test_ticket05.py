import asyncio
import uuid

import asyncpg
import pytest
from fastapi.testclient import TestClient

from varesense.core.config import settings
from varesense.core.security import hash_password, verify_password
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
                "edge_nodes, device_credentials, device_endpoints "
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


async def _fetch_credentials(node_id):
    conn = await asyncpg.connect(_pg_url())
    try:
        rows = await conn.fetch(
            "SELECT * FROM device_credentials WHERE edge_node_id = $1", node_id
        )
    finally:
        await conn.close()
    return [dict(r) for r in rows]


def test_device_lifecycle_and_status(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    r = client.post("/v1/devices", headers=hdr, json={"name": "Node 1"})
    assert r.status_code == 200
    node = r.json()
    assert node["lifecycle_state"] == "provisioning"
    assert node["device_status"] == "provisioning"

    assert client.patch(
        f"/v1/devices/{node['id']}", headers=hdr, json={"lifecycle_state": "active"}
    ).json()["lifecycle_state"] == "active"
    assert client.patch(
        f"/v1/devices/{node['id']}", headers=hdr, json={"lifecycle_state": "retired"}
    ).json()["lifecycle_state"] == "retired"
    # retired is terminal; cannot go back
    assert client.patch(
        f"/v1/devices/{node['id']}", headers=hdr, json={"lifecycle_state": "active"}
    ).status_code == 400

    # device_status is independent of lifecycle and freely settable
    assert client.patch(
        f"/v1/devices/{node['id']}", headers=hdr, json={"device_status": "online"}
    ).json()["device_status"] == "online"

    # retired node cannot receive new credentials
    assert client.post(
        f"/v1/devices/{node['id']}/credentials", headers=hdr, json={}
    ).status_code == 400


def test_cross_tenant_blocked(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node = client.post("/v1/devices", headers=hdr, json={"name": "N"}).json()

    _run(_seed_org_b())
    b_login = client.post("/v1/auth/login", json={"email": "op@beta.io", "password": "s3cret"})
    b_hdr = {"Authorization": f"Bearer {b_login.json()['access_token']}"}

    assert client.get(f"/v1/devices/{node['id']}", headers=b_hdr).status_code == 404
    assert client.patch(
        f"/v1/devices/{node['id']}", headers=b_hdr, json={"name": "hacked"}
    ).status_code == 404
    assert client.post(
        f"/v1/devices/{node['id']}/credentials", headers=b_hdr, json={}
    ).status_code == 404


def test_credential_issue_rotate_revoke(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node = client.post("/v1/devices", headers=hdr, json={"code": "edge-01", "name": "N"}).json()

    issued = client.post(f"/v1/devices/{node['id']}/credentials", headers=hdr, json={})
    assert issued.status_code == 200
    cred = issued.json()
    assert cred["secret"]
    assert cred["revoked_at"] is None

    creds = _run(_fetch_credentials(node["id"]))
    assert len(creds) == 1
    # plaintext never stored; hash verifies instead
    assert creds[0]["key_hash"] != cred["secret"]
    assert verify_password(cred["secret"], creds[0]["key_hash"])

    rotated = client.post(
        f"/v1/devices/{node['id']}/credentials/{cred['id']}/rotate", headers=hdr
    )
    assert rotated.status_code == 200
    new = rotated.json()
    assert new["secret"] != cred["secret"]
    assert new["id"] != cred["id"]

    creds = _run(_fetch_credentials(node["id"]))
    revoked_old = [c for c in creds if c["id"] == uuid.UUID(cred["id"])]
    assert revoked_old[0]["revoked_at"] is not None

    revoked = client.post(
        f"/v1/devices/{node['id']}/credentials/{new['id']}/revoke", headers=hdr
    )
    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None

    # rotate on an already-revoked credential is rejected
    assert client.post(
        f"/v1/devices/{node['id']}/credentials/{new['id']}/rotate", headers=hdr
    ).status_code == 400


def test_endpoint_mapping(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node = client.post("/v1/devices", headers=hdr, json={"name": "N"}).json()
    site = client.post("/v1/assets", headers=hdr, json={"type": "site", "name": "Plant"}).json()

    # site is not a valid mapping target (machines/components only)
    assert client.post(
        f"/v1/devices/{node['id']}/endpoints",
        headers=hdr,
        json={"kind": "sensor", "name": "Vibe", "asset_id": site["id"]},
    ).status_code == 400

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

    r = client.post(
        f"/v1/devices/{node['id']}/endpoints",
        headers=hdr,
        json={"kind": "sensor", "name": "Vibe", "asset_id": machine["id"]},
    )
    assert r.status_code == 200
    assert r.json()["asset_id"] == machine["id"]

    lst = client.get(f"/v1/devices/{node['id']}/endpoints", headers=hdr)
    assert lst.status_code == 200
    assert [e["kind"] for e in lst.json()] == ["sensor"]


def test_device_audit_written(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    node = client.post("/v1/devices", headers=hdr, json={"name": "N"}).json()
    client.post(f"/v1/devices/{node['id']}/credentials", headers=hdr, json={})

    async def _fetch_actions():
        conn = await asyncpg.connect(_pg_url())
        try:
            rows = await conn.fetch("SELECT action FROM audit_log ORDER BY created_at")
        finally:
            await conn.close()
        return [r["action"] for r in rows]

    actions = _run(_fetch_actions())
    assert "create_device" in actions
    assert "issue_device_credential" in actions
