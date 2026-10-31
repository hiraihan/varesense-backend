from fastapi import FastAPI, HTTPException
from sqlalchemy import text

from varesense.assets.router import router as assets_router
from varesense.auth.router import router as auth_router
from varesense.core.db import engine
from varesense.core.logging import setup_logging

setup_logging()

app = FastAPI(title="Varesense.ai", version="0.1.0")
app.include_router(auth_router, prefix="/v1")
app.include_router(assets_router, prefix="/v1")


@app.get("/v1/health")
async def health():
    return {"status": "ok"}


@app.get("/v1/ready")
async def ready():
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(status_code=503, detail="database unreachable")
    return {"status": "ok", "database": "ok"}
