from fastapi.testclient import TestClient

from varesense.core.security import hash_password, verify_password
from varesense.main import app

client = TestClient(app)


def test_health():
    r = client.get("/v1/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_ready_reports_database():
    r = client.get("/v1/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"


def test_login_requires_auth():
    r = client.get("/v1/auth/me")
    assert r.status_code == 401


def test_password_hash_roundtrip():
    h = hash_password("s3cret")
    assert h.startswith("pbkdf2_sha256$")
    assert verify_password("s3cret", h)
    assert not verify_password("wrong", h)
