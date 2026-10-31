import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from varesense.analytics.models import ANOMALY_STATUSES, Anomaly, Baseline
from varesense.analytics.schemas import (
    AnomalyOut,
    AnomalyUpdate,
    BaselineCreate,
    BaselineOut,
    TrendOut,
)
from varesense.analytics.service import compute_baseline, detect_anomalies, query_trends
from varesense.audit.service import record_audit
from varesense.auth.deps import get_current_user, require_role
from varesense.auth.models import User
from varesense.core.db import get_session

router = APIRouter(prefix="/analytics", tags=["analytics"])

ANOMALY_TRANSITIONS = {"pending": {"reviewed"}, "reviewed": set()}


@router.get("/trends", response_model=TrendOut)
async def trends(
    asset_id: uuid.UUID,
    measurement_type: str,
    quality: str = "valid",
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 100,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await query_trends(
        session,
        user.organization_id,
        asset_id,
        measurement_type,
        quality,
        start,
        end,
        min(limit, 1000),
    )


@router.post("/baselines", response_model=BaselineOut, status_code=201)
async def create_baseline(
    body: BaselineCreate,
    admin: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
):
    baseline = await compute_baseline(session, admin.organization_id, body)
    await record_audit(
        session,
        actor_user_id=admin.id,
        action="create_baseline",
        object_type="baseline",
        object_id=baseline.id,
        details={"asset_id": str(baseline.asset_id), "measurement_type": baseline.measurement_type},
    )
    await session.commit()
    await session.refresh(baseline)
    return baseline


@router.get("/baselines", response_model=list[BaselineOut])
async def list_baselines(
    asset_id: uuid.UUID | None = None,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Baseline).where(Baseline.organization_id == user.organization_id)
    if asset_id is not None:
        stmt = stmt.where(Baseline.asset_id == asset_id)
    result = await session.execute(stmt.order_by(Baseline.created_at.desc()))
    return result.scalars().all()


@router.get("/anomalies", response_model=list[AnomalyOut])
async def list_anomalies(
    asset_id: uuid.UUID | None = None,
    status_filter: str | None = None,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if status_filter is not None and status_filter not in ANOMALY_STATUSES:
        raise HTTPException(http_status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid status filter")
    stmt = select(Anomaly).where(Anomaly.organization_id == user.organization_id)
    if asset_id is not None:
        stmt = stmt.where(Anomaly.asset_id == asset_id)
    if status_filter is not None:
        stmt = stmt.where(Anomaly.status == status_filter)
    result = await session.execute(stmt.order_by(Anomaly.measured_at.desc()))
    return result.scalars().all()


@router.post("/anomalies/run")
async def run_anomaly_detection(
    z_threshold: float = 3.0,
    admin: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
):
    created = await detect_anomalies(session, admin.organization_id, z_threshold)
    await record_audit(
        session,
        actor_user_id=admin.id,
        action="run_anomaly_detection",
        object_type="anomaly",
        details={"created": created, "z_threshold": z_threshold},
    )
    await session.commit()
    return {"created": created}


@router.patch("/anomalies/{anomaly_id}", response_model=AnomalyOut)
async def review_anomaly(
    anomaly_id: uuid.UUID,
    body: AnomalyUpdate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    anomaly = await session.get(Anomaly, anomaly_id)
    if anomaly is None or anomaly.organization_id != user.organization_id:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "anomaly not found")
    if body.status not in ANOMALY_TRANSITIONS[anomaly.status]:
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            f"cannot transition {anomaly.status} -> {body.status}",
        )
    previous = anomaly.status
    anomaly.status = body.status
    await record_audit(
        session,
        actor_user_id=user.id,
        action="anomaly_review",
        object_type="anomaly",
        object_id=anomaly.id,
        details={"from": previous, "to": body.status},
    )
    await session.commit()
    await session.refresh(anomaly)
    return anomaly
