import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Float, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from varesense.core.db import Base
from varesense.core.model import TimestampMixin, UUIDPkMixin

QUALITY_STATUSES = (
    "valid",
    "stale",
    "invalid",
    "out-of-range",
    "sensor-fault",
    "estimated",
    "unknown",
)


class TelemetrySample(Base):
    """One measurement or device-state record (ADR-0001 envelope).

    Backed by a time-partitioned table (`telemetry`); `measured_at` is the
    partition key. Dedupe is enforced by a unique index on
    (device_id, event_id, measured_at).
    """

    __tablename__ = "telemetry"
    __table_args__ = (
        Index("ix_telemetry_org_time", "organization_id", "measured_at"),
    )

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    measured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    sequence_id: Mapped[int] = mapped_column(BigInteger)
    measurement_type: Mapped[str] = mapped_column(String(64))
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(32))
    quality_status: Mapped[str] = mapped_column(String(32))
    operating_context: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    schema_version: Mapped[str] = mapped_column(String(32))
    configuration_version: Mapped[str | None] = mapped_column(String(64))
    firmware_version: Mapped[str | None] = mapped_column(String(64))


class DeadLetter(UUIDPkMixin, TimestampMixin, Base):
    """Invalid or unroutable telemetry, never silently dropped (ADR-0001)."""

    __tablename__ = "dead_letter"
    __table_args__ = (Index("ix_dead_letter_org", "organization_id"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    device_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    event_id: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(String(255))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
