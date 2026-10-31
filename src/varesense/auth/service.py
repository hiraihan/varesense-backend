import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from varesense.audit.service import record_audit
from varesense.auth.models import RefreshToken, User
from varesense.core.config import settings
from varesense.core.security import (
    TOKEN_KIND_ACCESS,
    TOKEN_KIND_REFRESH,
    create_token,
    decode_token,
    hash_password,
    verify_password,
)


async def authenticate(
    session: AsyncSession, email: str, password: str
) -> User | None:
    result = await session.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(password, user.hashed_password):
        return None
    return user


async def create_user(
    session: AsyncSession,
    email: str,
    password: str,
    organization_id: uuid.UUID,
    role: str = "operator",
    actor_user_id: uuid.UUID | None = None,
) -> User:
    user = User(
        email=email,
        hashed_password=hash_password(password),
        organization_id=organization_id,
        role=role,
    )
    session.add(user)
    await session.flush()
    await record_audit(
        session,
        actor_user_id=actor_user_id,
        action="create_user",
        object_type="user",
        object_id=user.id,
        details={"email": email, "role": role},
    )
    await session.commit()
    await session.refresh(user)
    return user


def issue_token_pair(user: User) -> dict:
    return {
        "access_token": create_token(
            str(user.id),
            settings.access_token_ttl_minutes,
            {
                "org": str(user.organization_id),
                "role": user.role,
                "type": TOKEN_KIND_ACCESS,
            },
        ),
        "refresh_token": create_token(
            str(user.id),
            settings.refresh_token_ttl_days * 24 * 60,
            {"type": TOKEN_KIND_REFRESH},
        ),
    }


async def persist_refresh(
    session: AsyncSession, user_id: uuid.UUID, refresh_token: str
) -> None:
    """Record an issued refresh token so rotation can revoke it."""
    payload = decode_token(refresh_token)
    session.add(
        RefreshToken(
            user_id=user_id,
            jti=payload["jti"],
            expires_at=datetime.fromtimestamp(payload["exp"], tz=timezone.utc),
        )
    )


async def rotate_refresh(session: AsyncSession, refresh_token: str) -> User:
    """Validate + revoke a refresh token, returning its user.

    The presented token is revoked before a new pair is issued, so reuse of an
    old refresh token fails (rotation).
    """
    try:
        payload = decode_token(refresh_token)
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
    if payload.get("type") != TOKEN_KIND_REFRESH:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")

    stored = await session.scalar(
        select(RefreshToken).where(RefreshToken.jti == payload.get("jti"))
    )
    if stored is None or stored.revoked_at is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")
    expires = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
    if expires <= datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token")

    user = await session.scalar(select(User).where(User.id == uuid.UUID(payload["sub"])))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user not found")

    stored.revoked_at = datetime.now(timezone.utc)
    return user
