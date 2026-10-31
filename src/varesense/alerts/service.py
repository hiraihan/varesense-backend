import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from varesense.alerts.models import Alert, AlertRule
from varesense.devices.models import DeviceEndpoint, EdgeNode
from varesense.telemetry.schemas import TelemetryEnvelope

TRANSITIONS = {
    "detected": {"open"},
    "open": {"acknowledged", "assigned_investigating"},
    "acknowledged": {"assigned_investigating", "action_recorded"},
    "assigned_investigating": {"action_recorded"},
    "action_recorded": {"resolved"},
    "resolved": {"closed"},
    "closed": set(),
}

# A new occurrence raises a new alert only once the prior incident is terminal
# (resolved/closed). While still actively handled, dedupe suppresses repeats.
ACTIVE_STATUSES = (
    "detected",
    "open",
    "acknowledged",
    "assigned_investigating",
    "action_recorded",
)


def validate_transition(current: str, new: str) -> None:
    if new not in TRANSITIONS[current]:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"cannot transition {current} -> {new}",
        )


def _crossed(operator: str, actual: float, threshold: float) -> bool:
    return {
        "gt": actual > threshold,
        "gte": actual >= threshold,
        "lt": actual < threshold,
        "lte": actual <= threshold,
    }[operator]


async def evaluate_envelope(
    session: AsyncSession, device: EdgeNode, envelope: TelemetryEnvelope
) -> list[Alert]:
    """Evaluate active rules; create alerts (P1 group/dedup). Returns created alerts."""
    asset_rows = await session.execute(
        select(DeviceEndpoint.asset_id).where(DeviceEndpoint.edge_node_id == device.id)
    )
    asset_ids = sorted({row[0] for row in asset_rows.all()})

    rules = (
        await session.execute(
            select(AlertRule).where(
                AlertRule.organization_id == device.organization_id,
                AlertRule.active.is_(True),
                AlertRule.parameter == envelope.measurement_type,
            )
        )
    ).scalars().all()

    created: list[Alert] = []
    for rule in rules:
        if rule.asset_id is not None and rule.asset_id not in asset_ids:
            continue
        if not _crossed(rule.operator, envelope.value, rule.value):
            continue
        target_asset = rule.asset_id or (asset_ids[0] if asset_ids else None)

        # Dedup: skip while an active (not terminal) alert exists for this rule+asset.
        existing = await session.scalar(
            select(Alert).where(
                Alert.organization_id == device.organization_id,
                Alert.rule_id == rule.id,
                Alert.asset_id == target_asset,
                Alert.status.in_(ACTIVE_STATUSES),
            )
        )
        if existing is not None:
            continue

        alert = Alert(
            organization_id=device.organization_id,
            rule_id=rule.id,
            asset_id=target_asset,
            device_id=device.id,
            parameter=envelope.measurement_type,
            value=envelope.value,
            unit=rule.unit,
            severity=rule.severity,
            source={
                "schema_version": envelope.schema_version,
                "configuration_version": envelope.configuration_version,
                "firmware_version": envelope.firmware_version,
            },
            evidence={
                "value": envelope.value,
                "unit": rule.unit,
                "threshold": rule.value,
                "operator": rule.operator,
                "measured_at": envelope.measured_at.isoformat(),
                "event_id": envelope.event_id,
            },
        )
        session.add(alert)
        created.append(alert)

    return created
