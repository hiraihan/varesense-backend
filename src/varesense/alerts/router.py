import uuid

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from varesense.alerts.models import ALERT_STATUSES, SEVERITIES, Alert, AlertRule
from varesense.alerts.schemas import (
    AlertOut,
    AlertTransition,
    RuleCreate,
    RuleOut,
    RuleUpdate,
)
from varesense.alerts.service import validate_transition
from varesense.assets.service import get_owned_asset
from varesense.audit.service import record_audit
from varesense.auth.deps import get_current_user, require_role
from varesense.auth.models import User
from varesense.core.db import get_session

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.post("/rules", response_model=RuleOut, status_code=201)
async def create_rule(
    body: RuleCreate,
    admin: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
):
    if body.asset_id is not None:
        await get_owned_asset(session, admin.organization_id, body.asset_id)
    rule = AlertRule(
        rule_key=uuid.uuid4(),
        organization_id=admin.organization_id,
        version=1,
        **body.model_dump(exclude={"asset_id"}),
        asset_id=body.asset_id,
    )
    session.add(rule)
    await session.flush()
    await record_audit(
        session,
        actor_user_id=admin.id,
        action="create_alert_rule",
        object_type="alert_rule",
        object_id=rule.id,
        details={"rule_key": str(rule.rule_key), "version": rule.version},
    )
    await session.commit()
    await session.refresh(rule)
    return rule


@router.get("/rules", response_model=list[RuleOut])
async def list_rules(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(AlertRule)
        .where(
            AlertRule.organization_id == user.organization_id,
            AlertRule.active.is_(True),
        )
        .order_by(AlertRule.created_at)
    )
    return result.scalars().all()


@router.get("/rules/{rule_key}", response_model=list[RuleOut])
async def rule_history(
    rule_key: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(AlertRule)
        .where(
            AlertRule.organization_id == user.organization_id,
            AlertRule.rule_key == rule_key,
        )
        .order_by(AlertRule.version.desc())
    )
    return result.scalars().all()


@router.patch("/rules/{rule_key}", response_model=RuleOut)
async def update_rule(
    rule_key: uuid.UUID,
    body: RuleUpdate,
    admin: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
):
    latest = await session.scalar(
        select(AlertRule)
        .where(
            AlertRule.organization_id == admin.organization_id,
            AlertRule.rule_key == rule_key,
            AlertRule.active.is_(True),
        )
        .order_by(AlertRule.version.desc())
        .limit(1)
    )
    if latest is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "rule not found")

    fields = body.model_fields_set
    values = {
        "name": latest.name,
        "parameter": latest.parameter,
        "operator": latest.operator,
        "value": latest.value,
        "unit": latest.unit,
        "severity": latest.severity,
    }
    for f in ("name", "operator", "value", "unit", "severity"):
        if f in fields:
            values[f] = getattr(body, f)

    new_asset = body.asset_id if "asset_id" in fields else latest.asset_id
    if new_asset is not None:
        await get_owned_asset(session, admin.organization_id, new_asset)
    latest.active = False
    rule = AlertRule(
        rule_key=rule_key,
        organization_id=admin.organization_id,
        version=latest.version + 1,
        asset_id=new_asset,
        **values,
    )
    try:
        session.add(rule)
        await session.flush()
        await record_audit(
            session,
            actor_user_id=admin.id,
            action="update_alert_rule",
            object_type="alert_rule",
            object_id=rule.id,
            details={"rule_key": str(rule_key), "version": rule.version},
        )
        await session.commit()
    except IntegrityError:
        # A concurrent update already created the next active version.
        await session.rollback()
        raise HTTPException(
            http_status.HTTP_409_CONFLICT,
            "concurrent rule update; reload and retry",
        )
    await session.refresh(rule)
    return rule


@router.delete("/rules/{rule_key}", status_code=204)
async def deactivate_rule(
    rule_key: uuid.UUID,
    admin: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
):
    latest = await session.scalar(
        select(AlertRule)
        .where(
            AlertRule.organization_id == admin.organization_id,
            AlertRule.rule_key == rule_key,
            AlertRule.active.is_(True),
        )
        .order_by(AlertRule.version.desc())
        .limit(1)
    )
    if latest is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "rule not found")
    latest.active = False
    await record_audit(
        session,
        actor_user_id=admin.id,
        action="deactivate_alert_rule",
        object_type="alert_rule",
        object_id=latest.id,
        details={"rule_key": str(rule_key)},
    )
    await session.commit()


@router.get("", response_model=list[AlertOut])
async def list_alerts(
    status: str | None = None,
    severity: str | None = None,
    asset_id: uuid.UUID | None = None,
    rule_id: uuid.UUID | None = None,
    limit: int = 50,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if status is not None and status not in ALERT_STATUSES:
        raise HTTPException(http_status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid status filter")
    if severity is not None and severity not in SEVERITIES:
        raise HTTPException(http_status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid severity filter")
    stmt = select(Alert).where(Alert.organization_id == user.organization_id)
    if status is not None:
        stmt = stmt.where(Alert.status == status)
    if severity is not None:
        stmt = stmt.where(Alert.severity == severity)
    if asset_id is not None:
        stmt = stmt.where(Alert.asset_id == asset_id)
    if rule_id is not None:
        stmt = stmt.where(Alert.rule_id == rule_id)
    result = await session.execute(
        stmt.order_by(Alert.created_at.desc()).limit(min(limit, 200))
    )
    return result.scalars().all()


@router.post("/{alert_id}/transition", response_model=AlertOut)
async def transition_alert(
    alert_id: uuid.UUID,
    body: AlertTransition,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    alert = await session.get(Alert, alert_id)
    if alert is None or alert.organization_id != user.organization_id:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "alert not found")
    previous = alert.status
    validate_transition(previous, body.status)
    alert.status = body.status
    await record_audit(
        session,
        actor_user_id=user.id,
        action="alert_transition",
        object_type="alert",
        object_id=alert.id,
        details={"from": previous, "to": body.status},
    )
    await session.commit()
    await session.refresh(alert)
    return alert
