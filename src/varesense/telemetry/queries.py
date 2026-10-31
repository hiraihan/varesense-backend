import uuid
from datetime import datetime

from sqlalchemy.sql.elements import ColumnElement

from varesense.devices.models import DeviceEndpoint
from varesense.telemetry.models import TelemetrySample


def telemetry_scope_where(
    organization_id: uuid.UUID,
    *,
    asset_ids: list[uuid.UUID],
    measurement_type: str | None = None,
    quality: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    start_inclusive: bool = True,
    end_inclusive: bool = True,
) -> list[ColumnElement]:
    """Shared WHERE conditions for scoped telemetry (join + tenant + filters).

    Used by analytics, energy and reports so the scoped-telemetry contract is
    defined once. Callers build their own SELECT and apply `*conditions`.
    """
    conditions: list[ColumnElement] = [
        DeviceEndpoint.organization_id == organization_id,
        DeviceEndpoint.asset_id.in_(asset_ids),
        TelemetrySample.organization_id == organization_id,
    ]
    if measurement_type is not None:
        conditions.append(TelemetrySample.measurement_type == measurement_type)
    if quality is not None:
        conditions.append(TelemetrySample.quality_status == quality)
    if start is not None:
        op = (
            TelemetrySample.measured_at.__gt__
            if not start_inclusive
            else TelemetrySample.measured_at.__ge__
        )
        conditions.append(op(start))
    if end is not None:
        op = (
            TelemetrySample.measured_at.__lt__
            if not end_inclusive
            else TelemetrySample.measured_at.__le__
        )
        conditions.append(op(end))
    return conditions
