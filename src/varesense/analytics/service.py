import uuid
from dataclasses import dataclass
from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from varesense.analytics.models import DEFAULT_METHOD_VERSION, Anomaly, Baseline
from varesense.analytics.schemas import BaselineCreate, TrendOut, TrendSampleOut, TrendSummaryOut
from varesense.alerts.models import Alert
from varesense.alerts.service import ACTIVE_STATUSES
from varesense.assets.service import get_owned_asset
from varesense.devices.models import DeviceEndpoint
from varesense.telemetry.models import TelemetrySample
from varesense.telemetry.queries import telemetry_scope_where

GRADUAL_MIN_STREAK = 3
ANOMALY_CHUNK = 1000


@dataclass(frozen=True)
class TelemetryFilter:
    organization_id: uuid.UUID
    asset_id: uuid.UUID
    measurement_type: str
    quality: str = "valid"
    start: datetime | None = None
    end: datetime | None = None
    start_inclusive: bool = True
    end_inclusive: bool = True


def _telemetry_for_asset(f: TelemetryFilter):
    return (
        select(TelemetrySample, DeviceEndpoint.id.label("sensor_id"))
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


async def _aggregate(session, sub, *, with_stddev: bool) -> tuple:
    cols = [func.count(sub.c.value), func.min(sub.c.value), func.max(sub.c.value), func.avg(sub.c.value)]
    if with_stddev:
        cols.append(func.stddev_samp(sub.c.value))
    row = (await session.execute(select(*cols).select_from(sub))).one()
    return tuple(row)


async def query_trends(
    session: AsyncSession,
    organization_id: uuid.UUID,
    asset_id: uuid.UUID,
    measurement_type: str,
    quality: str,
    start: datetime | None,
    end: datetime | None,
    limit: int,
) -> TrendOut:
    await get_owned_asset(session, organization_id, asset_id)

    f = TelemetryFilter(organization_id, asset_id, measurement_type, quality, start, end)
    base = _telemetry_for_asset(f)
    sub = base.subquery()

    count, mn, mx, mean = await _aggregate(session, sub, with_stddev=False)
    summary = TrendSummaryOut(count=count, min=mn, max=mx, mean=mean)

    rows = (await session.execute(base.order_by(TelemetrySample.measured_at.desc()).limit(limit))).all()
    samples = [
        TrendSampleOut(
            measured_at=sample.measured_at,
            value=sample.value,
            quality_status=sample.quality_status,
            unit=sample.unit,
            sensor_id=sensor_id,
        )
        for sample, sensor_id in rows
    ]
    return TrendOut(samples=samples, summary=summary)


async def compute_baseline(
    session: AsyncSession, organization_id: uuid.UUID, body: BaselineCreate
) -> Baseline:
    await get_owned_asset(session, organization_id, body.asset_id)

    f = TelemetryFilter(
        organization_id, body.asset_id, body.measurement_type, "valid", body.period_start, body.period_end
    )
    sub = _telemetry_for_asset(f).subquery()

    n, mn, mx, mean, std = await _aggregate(session, sub, with_stddev=True)
    if not n:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no valid telemetry in period")

    training_until = (
        await session.scalar(select(func.max(sub.c.measured_at)).select_from(sub))
    )
    baseline = Baseline(
        organization_id=organization_id,
        asset_id=body.asset_id,
        measurement_type=body.measurement_type,
        placement=body.placement,
        period_start=body.period_start,
        period_end=body.period_end,
        operating_context=body.operating_context,
        n=n,
        mean=float(mean),
        std=float(std) if std is not None else 0.0,
        min_value=float(mn),
        max_value=float(mx),
        training_until=training_until,
        method_version=DEFAULT_METHOD_VERSION,
    )
    session.add(baseline)
    await session.flush()
    return baseline


async def detect_anomalies(
    session: AsyncSession, organization_id: uuid.UUID, z_threshold: float
) -> int:
    baselines = (
        await session.execute(
            select(Baseline).where(
                Baseline.organization_id == organization_id,
                Baseline.active.is_(True),
            )
        )
    ).scalars().all()

    created = 0
    # One alert per asset+measurement incident; keep the most recent sample per
    # key so a batch run never opens a flood of alerts for the same signal.
    alert_candidates: dict[tuple[uuid.UUID, str], dict] = {}
    for bl in baselines:
        if bl.std <= 0:
            continue
        # Score only data observed after the baseline was fit, so training
        # samples never self-flag.
        f = TelemetryFilter(
            organization_id,
            bl.asset_id,
            bl.measurement_type,
            "valid",
            bl.training_until,
            bl.period_end,
            start_inclusive=False,
        )
        existing = {
            (a.device_id, a.measured_at)
            for a in (
                await session.execute(select(Anomaly).where(Anomaly.baseline_id == bl.id))
            ).scalars()
        }

        # Chunked cursor iteration over measured_at so a large history is never
        # loaded fully into memory in one batch run.
        streak_dir = 0
        streak = 0
        cursor: datetime | None = None
        while True:
            q = _telemetry_for_asset(f).order_by(TelemetrySample.measured_at)
            if cursor is not None:
                q = q.where(TelemetrySample.measured_at > cursor)
            rows = (await session.execute(q.limit(ANOMALY_CHUNK))).all()
            if not rows:
                break
            for sample, _sensor_id in rows:
                if (sample.device_id, sample.measured_at) in existing:
                    continue
                z = (sample.value - bl.mean) / bl.std
                if abs(z) < z_threshold:
                    streak_dir = 0
                    streak = 0
                    continue
                direction = 1 if z > 0 else -1
                streak = streak + 1 if direction == streak_dir else 1
                streak_dir = direction
                gradual = streak >= GRADUAL_MIN_STREAK

                session.add(
                    Anomaly(
                        organization_id=organization_id,
                        baseline_id=bl.id,
                        asset_id=bl.asset_id,
                        device_id=sample.device_id,
                        measurement_type=bl.measurement_type,
                        measured_at=sample.measured_at,
                        value=sample.value,
                        z_score=z,
                        gradual=gradual,
                        source={
                            "method": "z-score",
                            "method_version": DEFAULT_METHOD_VERSION,
                            "baseline_id": str(bl.id),
                        },
                        evidence={
                            "mean": bl.mean,
                            "std": bl.std,
                            "z_threshold": z_threshold,
                            "unit": sample.unit,
                        },
                    )
                )
                created += 1
                alert_candidates[(bl.asset_id, bl.measurement_type)] = {
                    "org_id": organization_id,
                    "asset_id": bl.asset_id,
                    "device_id": sample.device_id,
                    "parameter": bl.measurement_type,
                    "value": sample.value,
                    "unit": sample.unit,
                    "baseline_id": bl.id,
                    "measured_at": sample.measured_at,
                    "gradual": gradual,
                    "z": z,
                }
            cursor = rows[-1][0].measured_at
            if len(rows) < ANOMALY_CHUNK:
                break
    await _create_anomaly_alerts(session, alert_candidates)
    return created


async def _create_anomaly_alerts(
    session: AsyncSession, candidates: dict[tuple[uuid.UUID, str], dict]
) -> None:
    for (asset_id, parameter), c in candidates.items():
        active = await session.scalar(
            select(Alert.id).where(
                Alert.organization_id == c["org_id"],
                Alert.asset_id == asset_id,
                Alert.parameter == parameter,
                Alert.status.in_(ACTIVE_STATUSES),
            )
        )
        if active is not None:
            continue
        session.add(
            Alert(
                organization_id=c["org_id"],
                rule_id=None,
                asset_id=asset_id,
                device_id=c["device_id"],
                parameter=parameter,
                value=c["value"],
                unit=c["unit"],
                severity="warning",
                source={
                    "method": "z-score",
                    "method_version": DEFAULT_METHOD_VERSION,
                    "baseline_id": str(c["baseline_id"]),
                },
                evidence={
                    "mean": c["z"],
                    "gradual": c["gradual"],
                    "measured_at": c["measured_at"].isoformat(),
                },
            )
        )
