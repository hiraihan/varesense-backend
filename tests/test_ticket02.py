import asyncpg
import pytest
from fastapi.testclient import TestClient

from varesense.core.config import settings
from varesense.core.security import create_token, hash_password
from varesense.main import app

client = TestClient(app)


def _pg_url() -> str:
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
def clean_db():
    """Drop all rows so /auth/bootstrap is usable per test."""
    import asyncio

    async def _wipe():
        conn = await asyncpg.connect(_pg_url())
        try:
            await conn.execute("TRUNCATE users, organizations, assets RESTART IDENTITY CASCADE")
        finally:
            await conn.close()

    asyncio.run(_wipe())
    yield
    asyncio.run(_wipe())


def test_bootstrap_then_login(clean_db):
    r = client.post(
        "/v1/auth/bootstrap",
        json={"org_name": "Acme", "email": "admin@acme.io", "password": "s3cret"},
    )
    assert r.status_code == 200
    tokens = r.json()
    assert tokens["access_token"] and tokens["refresh_token"]

    me = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert me.status_code == 200
    assert me.json()["email"] == "admin@acme.io"
    assert me.json()["role"] == "admin"


def test_bootstrap_refuses_when_users_exist(clean_db):
    body = {"org_name": "Acme", "email": "admin@acme.io", "password": "s3cret"}
    assert client.post("/v1/auth/bootstrap", json=body).status_code == 200
    assert client.post("/v1/auth/bootstrap", json=body).status_code == 409


def test_create_user_requires_admin(clean_db):
    admin_boot = client.post(
        "/v1/auth/bootstrap",
        json={"org_name": "Acme", "email": "admin@acme.io", "password": "s3cret"},
    ).json()
    admin_hdr = {"Authorization": f"Bearer {admin_boot['access_token']}"}

    op = client.post(
        "/v1/auth/users",
        headers=admin_hdr,
        json={"email": "op@acme.io", "password": "s3cret", "role": "operator"},
    )
    assert op.status_code == 201
    assert op.json()["role"] == "operator"

    op_login = client.post(
        "/v1/auth/login", json={"email": "op@acme.io", "password": "s3cret"}
    )
    op_hdr = {"Authorization": f"Bearer {op_login.json()['access_token']}"}
    forbidden = client.post(
        "/v1/auth/users",
        headers=op_hdr,
        json={"email": "x@acme.io", "password": "s3cret", "role": "operator"},
    )
    assert forbidden.status_code == 403


def test_refresh_rotates_tokens(clean_db):
    boot = client.post(
        "/v1/auth/bootstrap",
        json={"org_name": "Acme", "email": "admin@acme.io", "password": "s3cret"},
    ).json()
    r = client.post("/v1/auth/refresh", json={"refresh_token": boot["refresh_token"]})
    assert r.status_code == 200
    new = r.json()
    assert new["access_token"] and new["refresh_token"]
    assert new["access_token"] != boot["access_token"]

    # refresh token must not work as an access token
    me = client.get(
        "/v1/auth/me", headers={"Authorization": f"Bearer {boot['refresh_token']}"}
    )
    assert me.status_code == 401


def test_expired_access_token_rejected(clean_db):
    boot = client.post(
        "/v1/auth/bootstrap",
        json={"org_name": "Acme", "email": "admin@acme.io", "password": "s3cret"},
    ).json()
    # craft an already-expired access token for the real booted user
    import jwt

    from varesense.core.config import settings

    payload = jwt.decode(
        boot["access_token"], settings.jwt_secret, algorithms=[settings.jwt_algorithm]
    )
    expired = create_token(
        payload["sub"], -1, {"type": "access", "org": payload["org"], "role": payload["role"]}
    )
    me = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert me.status_code == 401


def test_cross_tenant_isolation(clean_db):
    import asyncio

    async def _seed_org_b():
        import uuid as uuid_mod

        conn = await asyncpg.connect(_pg_url())
        try:
            org_id = uuid_mod.uuid4()
            await conn.execute(
                "INSERT INTO organizations (id, name) VALUES ($1, $2)",
                org_id,
                "Beta",
            )
            await conn.execute(
                "INSERT INTO users (id, organization_id, email, hashed_password, role) "
                "VALUES ($1, $2, $3, $4, 'operator')",
                uuid_mod.uuid4(),
                org_id,
                "op@beta.io",
                hash_password("s3cret"),
            )
        finally:
            await conn.close()

    admin_boot = client.post(
        "/v1/auth/bootstrap",
        json={"org_name": "Acme", "email": "admin@acme.io", "password": "s3cret"},
    ).json()
    admin_hdr = {"Authorization": f"Bearer {admin_boot['access_token']}"}

    asyncio.run(_seed_org_b())

    # admin (org A) creates an asset -> org A
    created = client.post(
        "/v1/assets",
        headers=admin_hdr,
        json={"type": "site", "name": "Plant A"},
    )
    assert created.status_code == 200
    assert created.json()["organization_id"]

    # user in org B must not see org A's asset
    b_login = client.post(
        "/v1/auth/login", json={"email": "op@beta.io", "password": "s3cret"}
    )
    b_hdr = {"Authorization": f"Bearer {b_login.json()['access_token']}"}
    b_list = client.get("/v1/assets", headers=b_hdr)
    assert b_list.status_code == 200
    assert b_list.json() == []

    a_list = client.get("/v1/assets", headers=admin_hdr)
    assert len(a_list.json()) == 1
