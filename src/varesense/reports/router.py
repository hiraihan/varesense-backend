import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from varesense.audit.service import record_audit
from varesense.auth.deps import get_current_user
from varesense.auth.models import User
from varesense.core.db import get_session
from varesense.reports.service import build_report, render_csv, render_pdf

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/export", response_class=Response)
async def export_report(
    scope: str = Query("organization"),
    asset_id: uuid.UUID | None = None,
    measurement_type: str | None = None,
    start: datetime = Query(...),
    end: datetime = Query(...),
    format: str = Query("csv"),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    report = await build_report(
        session,
        user.organization_id,
        scope=scope,
        asset_id=asset_id,
        measurement_type=measurement_type,
        start=start,
        end=end,
    )
    await record_audit(
        session,
        actor_user_id=user.id,
        action="report_export",
        object_type="report",
        details={
            "scope": scope,
            "asset_id": str(asset_id) if asset_id else None,
            "measurement_type": measurement_type,
            "format": format,
            "start": start.isoformat(),
            "end": end.isoformat(),
        },
    )
    await session.commit()

    if format == "csv":
        content: str | bytes = render_csv(report)
        media_type = "text/csv"
        ext = "csv"
    elif format == "pdf":
        content = render_pdf(report)
        media_type = "application/pdf"
        ext = "pdf"
    else:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "format must be csv or pdf")

    filename = f"varesense_{scope}_{start:%Y%m%d}-{end:%Y%m%d}.{ext}"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
