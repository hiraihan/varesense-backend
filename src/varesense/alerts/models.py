import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from varesense.core.db import Base
from varesense.core.model import TimestampMixin, UUIDPkMixin

OPERATORS = ("gt", "gte", "lt", "lte")
SEVERITIES = ("info", "warning", "critical")
ALERT_STATUSES = (
    "detected",
    "open",
    "acknowledged",
    "assigned_investigating",
    "action_recorded",
    "resolved",
    "closed",
)


class AlertRule(UUIDPkMixin, TimestampMixin, Base):
    """Versioned threshold rule; `rule_key` is the stable lineage id.

    Each edit inserts a new row with version+1 and deactivates the prior one,
    so history is preserved (change control). Only `active` rules evaluate.
    """

    __tablename__ = "alert_rules"
    __table_args__ = (
        # At most one active version per rule lineage; DB-level guard against
        # concurrent updates inserting duplicate active versions.
        Index(
            "uq_alert_rules_active_rulekey",
            "organization_id",
            "rule_key",
            unique=True,
            postgresql_where=text("active"),
        ),
    )

    rule_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    name: Mapped[str] = mapped_column(String(255))
    parameter: Mapped[str] = mapped_column(String(64))
    operator: Mapped[str] = mapped_column(String(8))
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16))
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id")
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Alert(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "alerts"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), index=True
    )
    rule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("alert_rules.id"), index=True
    )
    asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id")
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("edge_nodes.id")
    )
    parameter: Mapped[str] = mapped_column(String(64))
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16))
    source: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(32), default="detected")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
