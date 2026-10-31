import uuid
from typing import Any

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from varesense.core.db import Base
from varesense.core.model import TimestampMixin, UUIDPkMixin

ASSET_TYPES = ("site", "area", "production_line", "machine", "component")


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
