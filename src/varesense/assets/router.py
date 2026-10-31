import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from varesense.assets.models import Asset, AssetHistory
from varesense.assets.schemas import AssetCreate, AssetOut, AssetUpdate
from varesense.assets.service import (
    chain_hits,
    ensure_children_valid,
    get_owned_asset,
    resolve_parent,
)
from varesense.audit.service import record_audit
from varesense.auth.deps import get_current_user
from varesense.auth.models import User
from varesense.core.db import get_session

router = APIRouter(prefix="/assets", tags=["assets"])


def _snapshot(asset: Asset) -> dict:
    return {
        "type": asset.type,
        "name": asset.name,
        "code": asset.code,
        "parent_id": str(asset.parent_id) if asset.parent_id else None,
        "data": asset.data,
        "archived": asset.archived,
    }


async def _record_history(
    session: AsyncSession,
    asset: Asset,
    action: str,
    previous: dict,
    actor_user_id: uuid.UUID,
) -> None:
    session.add(
        AssetHistory(
            asset_id=asset.id,
            organization_id=asset.organization_id,
            action=action,
            previous=previous,
            changed_by_user_id=actor_user_id,
        )
    )


@router.post("", response_model=AssetOut)
async def create_asset(
    body: AssetCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    parent = await resolve_parent(session, user.organization_id, body.parent_id, body.type)
    asset = Asset(
        organization_id=user.organization_id,
        type=body.type,
        name=body.name,
        code=body.code,
        parent_id=parent.id if parent else None,
        data=body.data,
    )
    session.add(asset)
    await session.flush()
    await record_audit(
        session,
        actor_user_id=user.id,
        action="create_asset",
        object_type="asset",
        object_id=asset.id,
        details=body.model_dump(),
    )
    await session.commit()
    await session.refresh(asset)
    return asset


@router.get("", response_model=list[AssetOut])
async def list_assets(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Asset)
        .where(Asset.organization_id == user.organization_id)
        .order_by(Asset.created_at)
    )
    return result.scalars().all()


@router.get("/{asset_id}", response_model=AssetOut)
async def get_asset(
    asset_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await get_owned_asset(session, user.organization_id, asset_id)


@router.patch("/{asset_id}", response_model=AssetOut)
async def update_asset(
    asset_id: uuid.UUID,
    body: AssetUpdate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    asset = await get_owned_asset(session, user.organization_id, asset_id)
    previous = _snapshot(asset)
    fields = body.model_fields_set

    final_type = (
        body.type if ("type" in fields and body.type is not None) else asset.type
    )
    final_parent_id = body.parent_id if "parent_id" in fields else asset.parent_id
    if "type" in fields or "parent_id" in fields:
        parent = await resolve_parent(
            session, user.organization_id, final_parent_id, final_type
        )
        final_parent_id = parent.id if parent else None
        if final_parent_id:

            async def _parent_of(pid: uuid.UUID) -> uuid.UUID | None:
                p = await session.get(Asset, pid)
                return p.parent_id if p else None

            if await chain_hits(_parent_of, final_parent_id, asset.id):
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, "cannot set a cyclic asset hierarchy"
                )

    if final_type != asset.type:
        await ensure_children_valid(session, asset.id, final_type)

    changes: list[str] = []
    if "name" in fields and body.name is not None and body.name != asset.name:
        changes.append("name")
    if "code" in fields and body.code != asset.code:
        changes.append("code")
    if "data" in fields and body.data != asset.data:
        changes.append("data")
    if "archived" in fields and body.archived is not None and body.archived != asset.archived:
        changes.append("archived")
    if "type" in fields and body.type is not None and body.type != asset.type:
        changes.append("type")
    if "parent_id" in fields and final_parent_id != asset.parent_id:
        changes.append("parent_id")

    if not changes:
        return asset  # no-op: don't record spurious history/audit

    if "name" in changes and body.name is not None:
        asset.name = body.name
    if "code" in changes:
        asset.code = body.code
    if "data" in changes:
        asset.data = body.data
    if "archived" in changes and body.archived is not None:
        asset.archived = body.archived
    if "type" in changes and body.type is not None:
        asset.type = body.type
    if "parent_id" in changes:
        asset.parent_id = final_parent_id

    await _record_history(session, asset, "update_asset", previous, user.id)
    await record_audit(
        session,
        actor_user_id=user.id,
        action="update_asset",
        object_type="asset",
        object_id=asset.id,
        details={"changed": changes},
    )
    await session.commit()
    await session.refresh(asset)
    return asset
