import csv
import io
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from reportlab.lib import colors  # type: ignore[import-untyped]
from reportlab.lib.pagesizes import A4  # type: ignore[import-untyped]
from reportlab.lib.styles import getSampleStyleSheet  # type: ignore[import-untyped]
from reportlab.platypus import (  # type: ignore[import-untyped]
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from varesense.assets.models import Asset
from varesense.assets.service import get_owned_asset, get_subtree
from varesense.devices.models import DeviceEndpoint
from varesense.telemetry.models import TelemetrySample
from varesense.telemetry.queries import telemetry_scope_where

SCOPES = ("organization", "site", "area", "asset")


async def _scope_assets(
    session: AsyncSession,
    organization_id: uuid.UUID,
    scope: str,
    asset_id: uuid.UUID | None,
) -> list[Asset]:
    if scope == "organization":
        rows = await session.execute(
            select(Asset).where(Asset.organization_id == organization_id)
        )
        return list(rows.scalars().all())
    if asset_id is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"asset_id required for scope={scope}",
        )
    root = await get_owned_asset(session, organization_id, asset_id)
    return await get_subtree(session, root)


async def build_report(
    session: AsyncSession,
    organization_id: uuid.UUID,
    *,
    scope: str,
    asset_id: uuid.UUID | None,
    measurement_type: str | None,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    if scope not in SCOPES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"scope must be one of {SCOPES}",
        )
    if start >= end:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "start must be before end"
        )
    if scope == "organization" and asset_id is not None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "asset_id not allowed for scope=organization",
        )

    assets = await _scope_assets(session, organization_id, scope, asset_id)
    asset_ids = [a.id for a in assets]
    by_id = {a.id: a for a in assets}

    def _scope_query():
        q = (
            select(
                DeviceEndpoint.asset_id.label("asset_id"),
                TelemetrySample.measurement_type.label("measurement_type"),
                TelemetrySample.quality_status.label("quality_status"),
                TelemetrySample.event_id.label("event_id"),
                TelemetrySample.value.label("value"),
                TelemetrySample.unit.label("unit"),
            )
            .join(DeviceEndpoint, DeviceEndpoint.edge_node_id == TelemetrySample.device_id)
            .where(
                *telemetry_scope_where(
                    organization_id,
                    asset_ids=asset_ids,
                    measurement_type=measurement_type,
                    start=start,
                    end=end,
                    end_inclusive=False,
                )
            )
        )
        return q

    # Aggregated valid samples across the period: sum per asset per measurement type.
    valid_sub = _scope_query().subquery()
    valid = (
        select(
            valid_sub.c.asset_id,
            valid_sub.c.measurement_type,
            func.count(valid_sub.c.event_id).label("sample_count"),
            func.sum(valid_sub.c.value).label("value"),
            func.min(valid_sub.c.unit).label("unit"),
        )
        .select_from(valid_sub)
        .where(valid_sub.c.quality_status == "valid")
        .group_by(valid_sub.c.asset_id, valid_sub.c.measurement_type)
    )
    valid_rows = (await session.execute(valid)).all()

    # Data-quality gaps: non-valid samples in scope, never silently filled.
    gap_sub = _scope_query().subquery()
    gaps = (
        select(
            gap_sub.c.asset_id,
            gap_sub.c.quality_status,
            func.count(gap_sub.c.event_id).label("sample_count"),
        )
        .select_from(gap_sub)
        .where(gap_sub.c.quality_status != "valid")
        .group_by(gap_sub.c.asset_id, gap_sub.c.quality_status)
    )
    gap_rows = (await session.execute(gaps)).all()

    def _asset_name(aid: uuid.UUID) -> str:
        return by_id[aid].name

    return {
        "scope": scope,
        "scope_asset_id": asset_id,
        "period_start": start,
        "period_end": end,
        "measurement_type": measurement_type,
        "assets": [
            {"id": a.id, "name": a.name, "type": a.type} for a in assets
        ],
        "rows": [
            {
                "asset_id": r[0],
                "asset_name": _asset_name(r[0]),
                "asset_type": by_id[r[0]].type,
                "measurement_type": r[1],
                "sample_count": r[2],
                "value": r[3],
                "unit": r[4],
            }
            for r in valid_rows
        ],
        "gaps": [
            {
                "asset_id": g[0],
                "asset_name": _asset_name(g[0]),
                "quality_status": g[1],
                "sample_count": g[2],
            }
            for g in gap_rows
        ],
    }


def _coverage(report: dict[str, Any]) -> dict[uuid.UUID, int]:
    cov: dict[uuid.UUID, int] = {}
    for r in report["rows"]:
        cov[r["asset_id"]] = cov.get(r["asset_id"], 0) + r["sample_count"]
    return cov


def render_csv(report: dict[str, Any]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)

    def _line(*cells: Any) -> None:
        w.writerow(["#", *cells])

    _line("varesense energy export")
    _line("scope", report["scope"])
    _line("period_start", report["period_start"].isoformat())
    _line("period_end", report["period_end"].isoformat())
    _line("measurement_type", report["measurement_type"] or "all")
    _line(
        "assets_included",
        f"{len(report['assets'])}",
    )
    for a in report["assets"]:
        _line("  asset", f"{a['name']} ({a['type']})", a["id"])
    _line(
        "method",
        "on-read aggregation of valid telemetry (sum per asset per measurement type across scope)",
    )
    _line("assumptions", "telemetry linked via device endpoints; buckets in UTC; valid quality only")
    cov = _coverage(report)
    for a in report["assets"]:
        _line("coverage", a["name"], cov.get(a["id"], 0), "valid samples")
    for g in report["gaps"]:
        _line(
            "quality_gap",
            g["asset_name"],
            g["quality_status"],
            g["sample_count"],
            "(noted, excluded from sums)",
        )
    if not report["gaps"]:
        _line("quality_gaps", "none")

    w.writerow([])
    w.writerow(
        ["asset_name", "asset_type", "asset_id", "measurement_type",
         "sample_count", "value", "unit"]
    )
    for r in report["rows"]:
        w.writerow(
            [r["asset_name"], r["asset_type"], r["asset_id"],
             r["measurement_type"], r["sample_count"], r["value"], r["unit"]]
        )
    return buf.getvalue()


def render_pdf(report: dict[str, Any]) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4)
    styles = getSampleStyleSheet()

    story = [
        Paragraph("varesense energy export", styles["Title"]),
        Paragraph(f"scope: {report['scope']}", styles["BodyText"]),
        Paragraph(
            f"period: {report['period_start'].isoformat()} \u2192 "
            f"{report['period_end'].isoformat()}",
            styles["BodyText"],
        ),
        Paragraph(
            f"measurement_type: {report['measurement_type'] or 'all'}",
            styles["BodyText"],
        ),
        Spacer(1, 12),
    ]

    story.append(Paragraph("Assets included", styles["Heading2"]))
    asset_rows = [["name", "type", "id"]]
    asset_rows += [
        [a["name"], a["type"], str(a["id"])] for a in report["assets"]
    ]
    story.append(Table(asset_rows))
    story.append(Spacer(1, 12))

    story.append(Paragraph("Valid aggregation", styles["Heading2"]))
    data_rows = [["asset", "type", "measurement", "samples", "value", "unit"]]
    data_rows += [
        [
            r["asset_name"],
            r["asset_type"],
            r["measurement_type"],
            r["sample_count"],
            _fmt(r["value"]),
            r["unit"],
        ]
        for r in report["rows"]
    ]
    agg_table = Table(data_rows)
    agg_table.setStyle(
        TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey)])
    )
    story.append(agg_table)
    story.append(Spacer(1, 12))

    story.append(Paragraph("Quality gaps", styles["Heading2"]))
    gap_rows = [["asset", "quality", "samples"]]
    gap_rows += [
        [g["asset_name"], g["quality_status"], g["sample_count"]]
        for g in report["gaps"]
    ]
    story.append(Table(gap_rows))

    doc.build(story)
    return buf.getvalue()


def _fmt(value: Any) -> str:
    return "" if value is None else f"{value:g}"
