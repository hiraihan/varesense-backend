import hashlib
import hmac
import os
import uuid

import jwt
from jwt import PyJWTError

from varesense.core.config import settings


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return f"pbkdf2_sha256$200000${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, digest_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iters)
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except ValueError:
        return False


TOKEN_KIND_ACCESS = "access"
TOKEN_KIND_REFRESH = "refresh"


def create_token(subject: str, ttl_minutes: int, extra: dict | None = None) -> str:
    # jti guarantees tokens differ per issuance (refresh rotation).
    payload = {"sub": subject, "jti": uuid.uuid4().hex, "exp": _exp(ttl_minutes)}
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except PyJWTError as exc:  # expired, invalid, tampered
        raise ValueError(str(exc)) from exc


def _exp(minutes: int) -> int:
    import time

    return int(time.time()) + minutes * 60
