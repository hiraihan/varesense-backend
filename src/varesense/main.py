from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from fastapi import FastAPI, HTTPException
from sqlalchemy import select, text

from varesense.alerts.router import router as alerts_router
from varesense.analytics.router import router as analytics_router
from varesense.analytics.service import detect_anomalies
from varesense.assets.models import Organization
from varesense.assets.router import router as assets_router
from varesense.auth.router import router as auth_router
from varesense.core.config import settings
from varesense.core.db import SessionLocal, engine
from varesense.core.logging import setup_logging
from varesense.devices.router import router as devices_router
from varesense.energy.router import router as energy_router
from varesense.maintenance.router import router as maintenance_router
from varesense.realtime.router import router as realtime_router
from varesense.reports.router import router as reports_router
from varesense.telemetry.router import router as telemetry_router

setup_logging()


async def _run_anomaly_batch() -> None:
    """Detect anomalies for every org on a schedule (in-process scheduler).

    `ponytail:` single-process job; swap to a distributed worker/queue once the
    app runs more than one worker or needs coordination.
    """
    async with SessionLocal() as session:
        org_ids = (
            await session.execute(select(Organization.id))
        ).scalars().all()
        for org_id in org_ids:
            await detect_anomalies(session, org_id, 3.0)
        await session.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _run_anomaly_batch,
        "interval",
        minutes=settings.anomaly_detect_interval_minutes,
        next_run_time=datetime.now(timezone.utc) + timedelta(minutes=1),
        id="anomaly-batch",
    )
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Varesense.ai", version="0.1.0", lifespan=lifespan)
app.include_router(auth_router, prefix="/v1")
app.include_router(assets_router, prefix="/v1")
app.include_router(alerts_router, prefix="/v1")
app.include_router(analytics_router, prefix="/v1")
app.include_router(devices_router, prefix="/v1")
app.include_router(energy_router, prefix="/v1")
app.include_router(maintenance_router, prefix="/v1")
app.include_router(realtime_router, prefix="/v1")
app.include_router(reports_router, prefix="/v1")
app.include_router(telemetry_router, prefix="/v1")


@app.get("/v1/health")
async def health():
    return {"status": "ok"}


@app.get("/v1/ready")
async def ready():
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            await conn.execute(text("SELECT 1 FROM audit_log LIMIT 1"))
    except Exception:
        raise HTTPException(status_code=503, detail="database unreachable")
    return {"status": "ok", "database": "ok", "audit": "ok"}
