import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from varesense.alerts.models import Alert
from varesense.assets.service import get_owned_asset
from varesense.audit.service import record_audit
from varesense.maintenance.models import MaintenanceRecord
from varesense.maintenance.schemas import MaintenanceCreate


async def _resolve_alert(
    session: AsyncSession, organization_id: uuid.UUID, alert_id: uuid.UUID | None
) -> Alert | None:
    if alert_id is None:
        return None
    alert = await session.get(Alert, alert_id)
    if alert is None or alert.organization_id != organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "alert not found")
    return alert


async def create_maintenance_record(
    session: AsyncSession,
    organization_id: uuid.UUID,
    author_user_id: uuid.UUID,
    body: MaintenanceCreate,
) -> MaintenanceRecord:
    await get_owned_asset(session, organization_id, body.asset_id)
    alert = await _resolve_alert(session, organization_id, body.alert_id)
    if alert is not None and alert.asset_id != body.asset_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "alert does not belong to asset"
        )

    record = MaintenanceRecord(
        organization_id=organization_id,
        asset_id=body.asset_id,
        alert_id=body.alert_id,
        kind=body.kind,
        findings=body.findings,
        outcome=body.outcome,
        author_user_id=author_user_id,
    )
    session.add(record)
    await session.flush()
    await record_audit(
        session,
        actor_user_id=author_user_id,
        action="maintenance_record_create",
        object_type="maintenance_record",
        object_id=record.id,
        details={"kind": body.kind, "outcome": body.outcome},
    )
    return record


async def list_maintenance_records(
    session: AsyncSession,
    organization_id: uuid.UUID,
    asset_id: uuid.UUID | None = None,
    alert_id: uuid.UUID | None = None,
) -> list[MaintenanceRecord]:
    stmt = select(MaintenanceRecord).where(
        MaintenanceRecord.organization_id == organization_id
    )
    if asset_id is not None:
        stmt = stmt.where(MaintenanceRecord.asset_id == asset_id)
    if alert_id is not None:
        stmt = stmt.where(MaintenanceRecord.alert_id == alert_id)
    stmt = stmt.order_by(MaintenanceRecord.created_at.desc())
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_maintenance_record(
    session: AsyncSession, organization_id: uuid.UUID, record_id: uuid.UUID
) -> MaintenanceRecord:
    record = await session.get(MaintenanceRecord, record_id)
    if record is None or record.organization_id != organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "maintenance record not found")
    return record
