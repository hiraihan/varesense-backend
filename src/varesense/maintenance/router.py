import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from varesense.auth.deps import get_current_user
from varesense.auth.models import User
from varesense.core.db import get_session
from varesense.maintenance.schemas import MaintenanceCreate, MaintenanceOut
from varesense.maintenance.service import (
    create_maintenance_record,
    get_maintenance_record,
    list_maintenance_records,
)

router = APIRouter(prefix="/maintenance", tags=["maintenance"])


@router.post(
    "/records", response_model=MaintenanceOut, status_code=201
)
async def create_record(
    body: MaintenanceCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    record = await create_maintenance_record(
        session, user.organization_id, user.id, body
    )
    await session.commit()
    await session.refresh(record)
    return record


@router.get("/records", response_model=list[MaintenanceOut])
async def list_records(
    asset_id: uuid.UUID | None = Query(None),
    alert_id: uuid.UUID | None = Query(None),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await list_maintenance_records(
        session, user.organization_id, asset_id, alert_id
    )


@router.get("/records/{record_id}", response_model=MaintenanceOut)
async def get_record(
    record_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await get_maintenance_record(session, user.organization_id, record_id)
