import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from varesense.core.db import Base
from varesense.core.model import TimestampMixin, UUIDPkMixin

EDGE_LIFECYCLE_STATES = ("provisioning", "active", "retired")
DEVICE_STATUSES = ("provisioning", "online", "offline", "degraded")
ENDPOINT_KINDS = ("module", "sensor", "channel")


class EdgeNode(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "edge_nodes"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    code: Mapped[str | None] = mapped_column(String(100))
    lifecycle_state: Mapped[str] = mapped_column(String(20), default="provisioning")
    device_status: Mapped[str] = mapped_column(String(20), default="provisioning")
    data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DeviceCredential(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "device_credentials"

    edge_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("edge_nodes.id"), index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), index=True
    )
    client_id: Mapped[str] = mapped_column(String(128), unique=True)
    key_hash: Mapped[str] = mapped_column(String(255))
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeviceEndpoint(UUIDPkMixin, TimestampMixin, Base):
    """Module/Sensor/Channel collapsed to one table; `kind` discriminates.

    `ponytail:` split into per-kind tables if the kinds diverge beyond a
    JSONB blob can carry.
    """

    __tablename__ = "device_endpoints"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), index=True
    )
    edge_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("edge_nodes.id"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(255))
    code: Mapped[str | None] = mapped_column(String(100))
    asset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assets.id")
    )
    data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
