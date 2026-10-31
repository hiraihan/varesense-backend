import uuid
from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from varesense.core.db import Base
from varesense.core.model import TimestampMixin, UUIDPkMixin

GRANULARITIES = ("hour", "day", "week", "month")
AGGREGATIONS = ("sum", "avg", "min", "max", "count")
ORIGINS = ("measured", "calculated", "estimated")


class CarbonFactor(UUIDPkMixin, TimestampMixin, Base):
    """Configured CO2 emission factor with visible provenance."""

    __tablename__ = "carbon_factors"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(128))
    unit: Mapped[str] = mapped_column(String(32))  # input energy unit, e.g. kWh
    factor: Mapped[float] = mapped_column(Float)   # kg CO2e per unit
    source: Mapped[str] = mapped_column(String(255))
    version: Mapped[str] = mapped_column(String(32))
    basis: Mapped[str] = mapped_column(String(255))
    assumptions: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
