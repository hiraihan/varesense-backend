import uuid
from typing import Any

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from varesense.core.db import Base
from varesense.core.model import TimestampMixin, UUIDPkMixin

MAINTENANCE_KINDS = ("inspection", "work", "replacement")
MAINTENANCE_OUTCOMES = ("completed", "no_action_needed", "blocked", "deferred")


class MaintenanceRecord(UUIDPkMixin, TimestampMixin, Base):
    """A technician action (inspection/work/replacement) recorded against an asset.

    Optionally linked to the alert that triggered it (alert_id), making
    alert -> record traceable. Org-scoped like all tenant data.
    """

    __tablename__ = "maintenance_records"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), index=True
    )
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id"), index=True
    )
    alert_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("alerts.id")
    )
    kind: Mapped[str] = mapped_column(String(32))
    findings: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    outcome: Mapped[str] = mapped_column(String(32))
    author_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
