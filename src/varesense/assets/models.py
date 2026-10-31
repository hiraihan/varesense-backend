import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from varesense.core.db import Base
from varesense.core.model import TimestampMixin, UUIDPkMixin

ASSET_TYPES = ("site", "area", "production_line", "machine", "component")
ASSET_TYPE_ORDER = {t: i for i, t in enumerate(ASSET_TYPES)}


class Organization(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255))


class Asset(UUIDPkMixin, TimestampMixin, Base):
    """Canonical hierarchy as one table: Site → Area → ProductionLine → Machine → Component.

    Adjacency list via parent_id keeps the shape expressible and auditable
    while avoiding five near-identical tables. `ponytail:` split into per-type
    tables if assets gain type-specific columns that a JSONB blob can't carry.
    """

    __tablename__ = "assets"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), index=True
    )
    type: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(255))
    code: Mapped[str | None] = mapped_column(String(100))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id")
    )
    data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    archived: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AssetHistory(UUIDPkMixin, TimestampMixin, Base):
    """Snapshot of an asset's state before a change (append-only by convention)."""

    __tablename__ = "asset_history"

    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id"), index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id")
    )
    action: Mapped[str] = mapped_column(String(30))
    previous: Mapped[dict[str, Any]] = mapped_column(JSONB)
    changed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
