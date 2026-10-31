import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from varesense.devices.models import EdgeNode
from varesense.telemetry.service import ingest_envelope, write_dead_letter


async def handle_mqtt_message(
    session: AsyncSession, device: EdgeNode, payload: bytes
) -> str:
    """Entry point for a QoS-1 MQTT subscriber on a per-device topic.

    `payload` is the raw envelope bytes. Returns the same outcome string as the
    HTTP endpoint. Wiring to a broker (EMQX) is deployment-time: connect a
    subscriber per device credential and call this with the decoded client_id's
    EdgeNode. Not importing an MQTT client here keeps ingestion broker-agnostic
    and testable.
    """
    try:
        body: dict[str, Any] = json.loads(payload)
    except (ValueError, TypeError):
        await write_dead_letter(
            session,
            organization_id=device.organization_id,
            device_id=device.id,
            event_id=None,
            reason="invalid_json",
            payload={"raw": "non-JSON payload"},
        )
        return "dead-lettered: invalid_json"
    outcome, _ = await ingest_envelope(session, device, body)
    # `ponytail:` realtime push for MQTT-born alerts requires the external broker
    # orchestrator to publish after its commit (this function does not own the
    # transaction). No live broker yet, so alerts here surface via HTTP only.
    return outcome
