import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from varesense.auth.models import User
from varesense.core.config import settings
from varesense.core.security import create_token, verify_password


async def authenticate(
    session: AsyncSession, email: str, password: str
) -> User | None:
    result = await session.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(password, user.hashed_password):
        return None
    return user


def issue_token_pair(user: User) -> dict:
    return {
        "access_token": create_token(
            str(user.id),
            settings.access_token_ttl_minutes,
            {"org": str(user.organization_id), "role": user.role},
        ),
        "refresh_token": create_token(
            str(user.id), settings.refresh_token_ttl_days * 24 * 60
        ),
    }
