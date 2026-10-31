from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from varesense.assets.models import Asset
from varesense.assets.schemas import AssetCreate, AssetOut
from varesense.auth.deps import get_current_user
from varesense.auth.models import User
from varesense.core.db import get_session
from varesense.core.rls import set_org_context

router = APIRouter(prefix="/assets", tags=["assets"])


@router.post("", response_model=AssetOut)
async def create_asset(
    body: AssetCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await set_org_context(session, str(user.organization_id))
    asset = Asset(organization_id=user.organization_id, **body.model_dump())
    session.add(asset)
    await session.commit()
    await session.refresh(asset)
    return asset


@router.get("", response_model=list[AssetOut])
async def list_assets(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await set_org_context(session, str(user.organization_id))
    result = await session.execute(select(Asset).order_by(Asset.created_at))
    return result.scalars().all()
