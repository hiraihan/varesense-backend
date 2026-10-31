import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from varesense.core.db import Base
from varesense.core.model import TimestampMixin, UUIDPkMixin

ANOMALY_STATUSES = ("pending", "reviewed")
DEFAULT_METHOD_VERSION = "v1"


class Baseline(UUIDPkMixin, TimestampMixin, Base):
    """Reference behavior for a machine/sensor/placement/period/context.

    `ponytail:` if analytics grows heavy aggregations, move to TimescaleDB
    (ADR-0001 gating) — here plain Postgres with computed stats at formation.
    """

    __tablename__ = "baselines"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), index=True
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id"), index=True
    )
    measurement_type: Mapped[str] = mapped_column(String(64))
    placement: Mapped[str | None] = mapped_column(String(255))
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    operating_context: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    n: Mapped[int] = mapped_column(Integer)
    mean: Mapped[float] = mapped_column(Float)
    std: Mapped[float] = mapped_column(Float)
    min_value: Mapped[float] = mapped_column(Float)
    max_value: Mapped[float] = mapped_column(Float)
    training_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    method_version: Mapped[str] = mapped_column(String(32), default=DEFAULT_METHOD_VERSION)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Anomaly(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "anomalies"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), index=True
    )
    baseline_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("baselines.id"), index=True
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id"), index=True
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("edge_nodes.id")
    )
    measurement_type: Mapped[str] = mapped_column(String(64))
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    value: Mapped[float] = mapped_column(Float)
    z_score: Mapped[float] = mapped_column(Float)
    gradual: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    source: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    evidence: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
