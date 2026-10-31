import uuid
from datetime import datetime
from typing import Any, Callable

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from varesense.analytics.service import TelemetryFilter
from varesense.assets.service import get_owned_asset
from varesense.devices.models import DeviceEndpoint
from varesense.energy.models import AGGREGATIONS, GRANULARITIES, CarbonFactor
from varesense.telemetry.models import TelemetrySample
from varesense.telemetry.queries import telemetry_scope_where

AGG: dict[str, Callable[[Any], Any]] = {
    "sum": func.sum,
    "avg": func.avg,
    "min": func.min,
    "max": func.max,
}


def _telemetry_energy(f: TelemetryFilter):
    return (
        select(TelemetrySample)
        .join(DeviceEndpoint, DeviceEndpoint.edge_node_id == TelemetrySample.device_id)
        .where(
            *telemetry_scope_where(
                f.organization_id,
                asset_ids=[f.asset_id],
                measurement_type=f.measurement_type,
                quality=f.quality,
                start=f.start,
                end=f.end,
                start_inclusive=f.start_inclusive,
                end_inclusive=f.end_inclusive,
            )
        )
    )


async def _sum_stats(session, f) -> tuple[float | None, int, str | None]:
    sub = _telemetry_energy(f).subquery()
    row = (
        await session.execute(
            select(func.sum(sub.c.value), func.count(sub.c.event_id), func.min(sub.c.unit)).select_from(sub)
        )
    ).one()
    return row[0], row[1], row[2]


def _validate_rollup(granularity: str, agg: str) -> None:
    if granularity not in GRANULARITIES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid granularity")
    if agg not in AGGREGATIONS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid aggregation")


async def select_rollup(
    session: AsyncSession,
    organization_id: uuid.UUID,
    f: TelemetryFilter,
    granularity: str,
    agg: str,
) -> dict:
    await get_owned_asset(session, organization_id, f.asset_id)
    _validate_rollup(granularity, agg)

    sub = _telemetry_energy(f).subquery()
    bucket = func.date_trunc(granularity, sub.c.measured_at).label("bucket")
    event_count = func.count(sub.c.event_id).label("cnt")
    value_expr = (
        func.count(sub.c.event_id) if agg == "count" else AGG[agg](sub.c.value)
    ).label("val")
    unit = await session.scalar(select(func.min(sub.c.unit)).select_from(sub))

    inner = select(bucket, value_expr, event_count).group_by(bucket).subquery()
    # delta vs previous bucket via a window function (on-read, no precompute)
    win = select(
        inner.c.bucket,
        inner.c.val,
        inner.c.cnt,
        func.lag(inner.c.val).over(order_by=inner.c.bucket).label("prev"),
    ).select_from(inner)
    rows = (await session.execute(win.order_by(inner.c.bucket))).all()

    return {
        "asset_id": f.asset_id,
        "measurement_type": f.measurement_type,
        "unit": unit,
        "granularity": granularity,
        "agg": agg,
        "buckets": [
            {
                "bucket": r[0],
                "value": r[1],
                "sample_count": r[2],
                "delta": None if r[3] is None else r[1] - r[3],
            }
            for r in rows
        ],
    }


async def select_trend(
    session: AsyncSession, organization_id: uuid.UUID, f: TelemetryFilter, limit: int
) -> dict:
    await get_owned_asset(session, organization_id, f.asset_id)
    rows = (
        await session.execute(
            _telemetry_energy(f).order_by(TelemetrySample.measured_at.desc()).limit(limit)
        )
    ).scalars().all()
    return {
        "asset_id": f.asset_id,
        "measurement_type": f.measurement_type,
        "points": [
            {
                "measured_at": r.measured_at,
                "value": r.value,
                "unit": r.unit,
                "quality_status": r.quality_status,
            }
            for r in rows
        ],
    }


async def compare_assets(session, organization_id, req) -> dict:
    a = await get_owned_asset(session, organization_id, req.asset_id_a)
    b = await get_owned_asset(session, organization_id, req.asset_id_b)

    fa = TelemetryFilter(organization_id, req.asset_id_a, req.measurement_type, "valid", req.start, req.end)
    fb = TelemetryFilter(organization_id, req.asset_id_b, req.measurement_type, "valid", req.start, req.end)
    at, an, aunit = await _sum_stats(session, fa)
    bt, bn, bunit = await _sum_stats(session, fb)
    assets = [
        {"asset_id": req.asset_id_a, "total": at, "unit": aunit, "sample_count": an},
        {"asset_id": req.asset_id_b, "total": bt, "unit": bunit, "sample_count": bn},
    ]

    flags: list[str] = []
    if a.type != b.type:
        flags.append("asset_types_differ")
    if aunit != bunit:
        flags.append("unit_mismatch")
    if an != bn:
        flags.append("sample_count_mismatch")
    if req.output_measurement_type:
        oa, _, _ = await _sum_stats(
            session, TelemetryFilter(organization_id, req.asset_id_a, req.output_measurement_type, "valid", req.start, req.end)
        )
        ob, _, _ = await _sum_stats(
            session, TelemetryFilter(organization_id, req.asset_id_b, req.output_measurement_type, "valid", req.start, req.end)
        )
        basis = "per-output-unit" if (oa and ob and oa > 0 and ob > 0) else "time"
        if not (oa and ob):
            flags.append("output_denominator_missing")
    else:
        basis = "time"
    return {"basis": basis, "assets": assets, "flags": flags}


async def estimate_carbon(session, organization_id, f, factor_id) -> dict:
    await get_owned_asset(session, organization_id, f.asset_id)
    factor = await session.get(CarbonFactor, factor_id)
    if factor is None or factor.organization_id != organization_id or not factor.active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "carbon factor not found")
    total, _, unit = await _sum_stats(session, f)
    if factor.unit != unit:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "carbon factor unit does not match telemetry unit",
        )
    return {
        "asset_id": f.asset_id,
        "measurement_type": f.measurement_type,
        "estimate": total * factor.factor,
        "unit": factor.unit,
        "factor": factor,
    }


async def compute_efficiency(session, organization_id, input_f, output_measurement_type) -> dict:
    await get_owned_asset(session, organization_id, input_f.asset_id)
    out_f = TelemetryFilter(
        organization_id,
        input_f.asset_id,
        output_measurement_type,
        "valid",
        input_f.start,
        input_f.end,
    )
    in_total, in_n, in_unit = await _sum_stats(session, input_f)
    out_total, out_n, out_unit = await _sum_stats(session, out_f)

    intensity = None
    efficiency = None
    if out_total is None or out_n == 0 or out_total <= 0:
        note = "no valid output denominator; returning consumption/trend only"
        origin = "measured"
    elif in_total is None or in_total <= 0:
        note = "no valid input consumption; cannot compute intensity/efficiency"
        origin = "measured"
    else:
        intensity = in_total / out_total
        efficiency = out_total / in_total
        note = "intensity input/output; efficiency output/input"
        origin = "calculated"

    return {
        "asset_id": input_f.asset_id,
        "input_measurement_type": input_f.measurement_type,
        "input_total": in_total,
        "input_unit": in_unit,
        "output_measurement_type": output_measurement_type,
        "output_total": out_total,
        "output_unit": out_unit,
        "intensity": intensity,
        "efficiency": efficiency,
        "basis_note": note,
        "origin": origin,
    }
