import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from varesense.auth.models import User
from varesense.core.db import get_session
from varesense.core.rls import set_org_context
from varesense.core.security import TOKEN_KIND_ACCESS, decode_token

bearer = HTTPBearer(auto_error=False)


async def resolve_user_from_token(
    session: AsyncSession, token: str, expected_kind: str
) -> User:
    """Decode + validate a token, returning its user or a 401."""
    try:
        payload = decode_token(token)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token"
        )
    if payload.get("type") != expected_kind:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token"
        )
    user = await session.scalar(
        select(User).where(User.id == uuid.UUID(payload["sub"]))
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="user not found"
        )
    return user


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: AsyncSession = Depends(get_session),
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="missing credentials"
        )
    user = await resolve_user_from_token(
        session, credentials.credentials, TOKEN_KIND_ACCESS
    )
    # Bind tenant RLS context before any downstream query (ADR-0002).
    await set_org_context(session, str(user.organization_id))
    return user


def require_role(*roles: str):
    async def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="insufficient privileges",
            )
        return user

    return checker
