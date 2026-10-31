import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from varesense.alerts.models import Alert
from varesense.alerts.service import evaluate_envelope
from varesense.devices.models import EdgeNode
from varesense.telemetry.models import DeadLetter, TelemetrySample
from varesense.telemetry.schemas import TelemetryEnvelope


async def ingest_sample(
    session: AsyncSession, node: EdgeNode, envelope: TelemetryEnvelope
) -> None:
    """Insert one sample, idempotent on (device_id, event_id, measured_at)."""
    stmt = (
        insert(TelemetrySample)
        .values(
            event_id=envelope.event_id,
            device_id=node.id,
            organization_id=node.organization_id,
            measured_at=envelope.measured_at,
            received_at=datetime.now(timezone.utc),
            sequence_id=envelope.sequence_id,
            measurement_type=envelope.measurement_type,
            value=envelope.value,
            unit=envelope.unit,
            quality_status=envelope.quality_status,
            operating_context=envelope.operating_context,
            schema_version=envelope.schema_version,
            configuration_version=envelope.configuration_version,
            firmware_version=envelope.firmware_version,
        )
        .on_conflict_do_nothing(
            index_elements=["device_id", "event_id", "measured_at"]
        )
    )
    await session.execute(stmt)


async def write_dead_letter(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    device_id: uuid.UUID | None,
    event_id: str | None,
    reason: str,
    payload: dict[str, Any],
) -> None:
    session.add(
        DeadLetter(
            organization_id=organization_id,
            device_id=device_id,
            event_id=event_id,
            reason=reason,
            payload=payload,
        )
    )


def _event_id_of(body: dict[str, Any]) -> str | None:
    raw = body.get("event_id")
    return raw if isinstance(raw, str) else None


async def ingest_envelope(
    session: AsyncSession, device: EdgeNode, body: dict[str, Any]
) -> tuple[str, list[Alert]]:
    """Validate + ingest one envelope; returns (outcome, created alerts).

    Shared by the HTTP ingestion endpoint and the MQTT subscriber so both go
    through the identical contract check → dead-letter → idempotent insert path.
    """
    try:
        envelope = TelemetryEnvelope(**body)
    except ValidationError:
        await write_dead_letter(
            session,
            organization_id=device.organization_id,
            device_id=device.id,
            event_id=_event_id_of(body),
            reason="validation_failed",
            payload=body,
        )
        return "dead-lettered: validation_failed", []

    if envelope.device_id != device.id:
        await write_dead_letter(
            session,
            organization_id=device.organization_id,
            device_id=device.id,
            event_id=envelope.event_id,
            reason="device_mismatch",
            payload=body,
        )
        return "dead-lettered: device_mismatch", []

    await ingest_sample(session, device, envelope)
    created = await evaluate_envelope(session, device, envelope)
    return "accepted", created
