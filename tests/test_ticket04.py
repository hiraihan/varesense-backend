import asyncio
import uuid

import asyncpg
import pytest
from fastapi.testclient import TestClient

from varesense.assets.service import chain_hits
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
                "TRUNCATE users, organizations, assets, asset_history, audit_log "
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


async def _fetch_history():
    conn = await asyncpg.connect(_pg_url())
    try:
        rows = await conn.fetch("SELECT * FROM asset_history ORDER BY created_at")
    finally:
        await conn.close()
    return [dict(r) for r in rows]


def test_build_hierarchy(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)

    site = client.post("/v1/assets", headers=hdr, json={"type": "site", "name": "Plant"})
    assert site.status_code == 200
    area = client.post(
        "/v1/assets", headers=hdr, json={"type": "area", "name": "Zone A", "parent_id": site.json()["id"]}
    )
    assert area.status_code == 200
    line = client.post(
        "/v1/assets",
        headers=hdr,
        json={"type": "production_line", "name": "Line 1", "parent_id": area.json()["id"]},
    )
    machine = client.post(
        "/v1/assets",
        headers=hdr,
        json={"type": "machine", "name": "M1", "parent_id": line.json()["id"]},
    )
    comp = client.post(
        "/v1/assets",
        headers=hdr,
        json={"type": "component", "name": "C1", "parent_id": machine.json()["id"]},
    )
    assert line.status_code == machine.status_code == comp.status_code == 200
    assert area.json()["parent_id"] == site.json()["id"]


def test_invalid_type_transition(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    site = client.post("/v1/assets", headers=hdr, json={"type": "site", "name": "Plant"}).json()

    # area under area
    area = client.post(
        "/v1/assets", headers=hdr, json={"type": "area", "name": "A", "parent_id": site["id"]}
    ).json()
    assert client.post(
        "/v1/assets", headers=hdr, json={"type": "area", "name": "B", "parent_id": area["id"]}
    ).status_code == 400
    # component under site
    assert client.post(
        "/v1/assets", headers=hdr, json={"type": "component", "name": "Y", "parent_id": site["id"]}
    ).status_code == 400
    # machine as root
    assert client.post(
        "/v1/assets", headers=hdr, json={"type": "machine", "name": "Z"}
    ).status_code == 400


def test_cross_tenant_blocked(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    site = client.post("/v1/assets", headers=hdr, json={"type": "site", "name": "Plant"}).json()

    _run(_seed_org_b())
    b_login = client.post("/v1/auth/login", json={"email": "op@beta.io", "password": "s3cret"})
    b_hdr = {"Authorization": f"Bearer {b_login.json()['access_token']}"}

    assert client.get(f"/v1/assets/{site['id']}", headers=b_hdr).status_code == 404
    assert client.patch(
        f"/v1/assets/{site['id']}", headers=b_hdr, json={"name": "hacked"}
    ).status_code == 404


def test_update_preserves_history_and_audit(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    site = client.post("/v1/assets", headers=hdr, json={"type": "site", "name": "Plant"}).json()

    r = client.patch(
        f"/v1/assets/{site['id']}", headers=hdr, json={"name": "Plant Renamed", "code": "P1"}
    )
    assert r.status_code == 200
    assert r.json()["name"] == "Plant Renamed"

    hist = _run(_fetch_history())
    assert any(
        h["action"] == "update_asset" and str(h["asset_id"]) == site["id"]
        for h in hist
    )


def test_archive_preserves_history(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    site = client.post("/v1/assets", headers=hdr, json={"type": "site", "name": "Plant"}).json()

    r = client.patch(f"/v1/assets/{site['id']}", headers=hdr, json={"archived": True})
    assert r.status_code == 200
    assert r.json()["archived"] is True
    hist = _run(_fetch_history())
    assert any(
        h["action"] == "update_asset" and str(h["asset_id"]) == site["id"]
        for h in hist
    )


def test_type_change_revalidates_children(clean_db):
    boot = _bootstrap()
    hdr = _admin_hdr(boot)
    site = client.post("/v1/assets", headers=hdr, json={"type": "site", "name": "Plant"}).json()
    area = client.post(
        "/v1/assets", headers=hdr, json={"type": "area", "name": "A", "parent_id": site["id"]}
    ).json()
    client.post(
        "/v1/assets", headers=hdr, json={"type": "production_line", "name": "L", "parent_id": area["id"]}
    )

    # re-type area -> machine would strand its production_line child
    r = client.patch(f"/v1/assets/{area['id']}", headers=hdr, json={"type": "machine"})
    assert r.status_code == 400


def test_cycle_rejected():
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    adj = {a: b, b: c, c: a}  # cycle a -> b -> c -> a

    async def ref(pid):
        return adj.get(pid)

    assert _run(chain_hits(ref, b, a)) is True
    assert _run(chain_hits(ref, a, c)) is True

    # acyclic path: target not reachable from start
    adj2 = {a: b, b: c}
    d = uuid.uuid4()

    async def ref2(pid):
        return adj2.get(pid)

    assert _run(chain_hits(ref2, a, d)) is False
