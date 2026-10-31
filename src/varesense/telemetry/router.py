from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from varesense.alerts.models import Alert
from varesense.auth.deps import require_role
from varesense.auth.models import User
from varesense.core.db import get_session
from varesense.devices.models import EdgeNode
from varesense.realtime.hub import hub
from varesense.telemetry.deps import get_current_device
from varesense.telemetry.models import DeadLetter
from varesense.telemetry.schemas import DeadLetterOut
from varesense.telemetry.service import ingest_envelope

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


def _alert_payload(alert: Alert) -> dict[str, Any]:
    return {
        "type": "alert",
        "id": str(alert.id),
        "rule_id": str(alert.rule_id),
        "asset_id": str(alert.asset_id) if alert.asset_id else None,
        "device_id": str(alert.device_id) if alert.device_id else None,
        "parameter": alert.parameter,
        "value": alert.value,
        "unit": alert.unit,
        "severity": alert.severity,
        "status": alert.status,
        "created_at": alert.created_at.isoformat() if alert.created_at else None,
    }


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def ingest(
    body: dict,
    device: EdgeNode = Depends(get_current_device),
    session: AsyncSession = Depends(get_session),
):
    outcome, created = await ingest_envelope(session, device, body)
    await session.commit()
    if outcome.startswith("dead-lettered"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, outcome)
    # Push only after commit so subscribers never observe a rolled-back alert.
    for alert in created:
        hub.publish(alert.organization_id, _alert_payload(alert))
    return {"status": "accepted"}


@router.get("/dead-letters", response_model=list[DeadLetterOut])
async def list_dead_letters(
    admin: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(DeadLetter)
        .where(DeadLetter.organization_id == admin.organization_id)
        .order_by(DeadLetter.created_at.desc())
        .limit(100)
    )
    return result.scalars().all()
