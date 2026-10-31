import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, model_validator

from varesense.telemetry.models import QUALITY_STATUSES


class TelemetryEnvelope(BaseModel):
    """Canonical telemetry envelope (ADR-0001)."""

    event_id: str
    device_id: uuid.UUID
    measured_at: datetime
    sequence_id: int
    measurement_type: str
    value: float
    unit: str
    quality_status: str
    operating_context: dict[str, Any] | None = None
    schema_version: str
    configuration_version: str | None = None
    firmware_version: str | None = None

    @model_validator(mode="after")
    def _validate(self):
        if not self.measurement_type.strip():
            raise ValueError("measurement_type is required")
        if not self.unit.strip():
            raise ValueError("unit is required")
        if self.quality_status not in QUALITY_STATUSES:
            raise ValueError(f"quality_status must be one of {QUALITY_STATUSES}")
        return self


class DeadLetterOut(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    device_id: uuid.UUID | None
    event_id: str | None
    reason: str
    created_at: datetime
