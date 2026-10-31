import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from varesense.assets.models import ASSET_TYPE_ORDER, Asset


async def get_owned_asset(
    session: AsyncSession, organization_id: uuid.UUID, asset_id: uuid.UUID
) -> Asset:
    asset = await session.get(Asset, asset_id)
    if asset is None or asset.organization_id != organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    return asset


async def get_subtree(session: AsyncSession, root: Asset) -> list[Asset]:
    """Root + descendants via adjacency-list walk (shared by reports)."""
    found = [root]
    frontier = [root.id]
    while frontier:
        rows = await session.execute(select(Asset).where(Asset.parent_id.in_(frontier)))
        children = list(rows.scalars().all())
        found.extend(children)
        frontier = [c.id for c in children]
    return found


async def resolve_parent(
    session: AsyncSession,
    organization_id: uuid.UUID,
    parent_id: uuid.UUID | None,
    child_type: str,
) -> Asset | None:
    """Return the validated parent, or None for a root (site-only)."""
    if parent_id is None:
        if child_type != "site":
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "only a site can be a root asset"
            )
        return None
    parent = await session.get(Asset, parent_id)
    if parent is None or parent.organization_id != organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "parent asset not found")
    if ASSET_TYPE_ORDER[child_type] != ASSET_TYPE_ORDER[parent.type] + 1:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"{child_type} must be a child of {parent.type}"
        )
    return parent


async def ensure_children_valid(
    session: AsyncSession, asset_id: uuid.UUID, new_type: str
) -> None:
    """Reject a type change that would leave a child under an invalid parent."""
    result = await session.execute(select(Asset).where(Asset.parent_id == asset_id))
    for child in result.scalars().all():
        if ASSET_TYPE_ORDER[child.type] != ASSET_TYPE_ORDER[new_type] + 1:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"{child.type} cannot remain under {new_type}",
            )


async def chain_hits(resolve, start_parent_id: uuid.UUID, asset_id: uuid.UUID) -> bool:
    """True if walking ancestors from start_parent_id ever reaches asset_id.

    Type-depth ordering makes cycles structurally impossible via the API, but
    this guard keeps the invariant defensive against future relaxations.
    """
    cur = start_parent_id
    seen: set[uuid.UUID] = set()
    while cur is not None and cur not in seen:
        if cur == asset_id:
            return True
        seen.add(cur)
        cur = await resolve(cur)
    return False
