from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from varesense.assets.models import Organization
from varesense.audit.service import record_audit
from varesense.auth.deps import get_current_user, require_role
from varesense.auth.models import User
from varesense.auth.schemas import (
    BootstrapRequest,
    LoginRequest,
    RefreshRequest,
    TokenResponse,
    UserCreate,
    UserOut,
)
from varesense.auth.service import (
    authenticate,
    create_user,
    issue_token_pair,
    persist_refresh,
    rotate_refresh,
)
from varesense.core.db import get_session

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/bootstrap", response_model=TokenResponse)
async def bootstrap(
    body: BootstrapRequest, session: AsyncSession = Depends(get_session)
):
    """Create the tenant's first org + admin. Only valid when no users exist."""
    count = await session.scalar(select(func.count()).select_from(User))
    if count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="already bootstrapped"
        )
    org = Organization(name=body.org_name)
    session.add(org)
    await session.flush()
    admin = await create_user(
        session, body.email, body.password, org.id, role="admin"
    )
    pair = issue_token_pair(admin)
    await persist_refresh(session, admin.id, pair["refresh_token"])
    await session.commit()
    return TokenResponse(**pair)


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user_endpoint(
    body: UserCreate,
    admin: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
):
    user = await create_user(
        session,
        body.email,
        body.password,
        admin.organization_id,
        body.role,
        actor_user_id=admin.id,
    )
    return user


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, session: AsyncSession = Depends(get_session)):
    user = await authenticate(session, body.email, body.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="bad email or password"
        )
    await record_audit(
        session,
        actor_user_id=user.id,
        action="login",
        object_type="user",
        object_id=user.id,
    )
    pair = issue_token_pair(user)
    await persist_refresh(session, user.id, pair["refresh_token"])
    await session.commit()
    return TokenResponse(**pair)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest, session: AsyncSession = Depends(get_session)
):
    user = await rotate_refresh(session, body.refresh_token)
    await record_audit(
        session,
        actor_user_id=user.id,
        action="refresh_token",
        object_type="user",
        object_id=user.id,
    )
    pair = issue_token_pair(user)
    await persist_refresh(session, user.id, pair["refresh_token"])
    await session.commit()
    return TokenResponse(**pair)


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)):
    return user
