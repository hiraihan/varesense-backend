import uuid
from datetime import datetime

from typing import Union

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from varesense.analytics.service import TelemetryFilter
from varesense.audit.service import record_audit
from varesense.auth.deps import get_current_user, require_role
from varesense.auth.models import User
from varesense.core.db import get_session
from varesense.energy.models import CarbonFactor
from varesense.energy.schemas import (
    CarbonEstimateOut,
    CarbonFactorCreate,
    CarbonFactorOut,
    CompareAssetOut,
    CompareOut,
    CompareRequest,
    EfficiencyOut,
    RollupOut,
    TrendOut,
)
from varesense.energy.service import (
    compare_assets,
    compute_efficiency,
    estimate_carbon,
    select_rollup,
    select_trend,
)

router = APIRouter(prefix="/energy", tags=["energy"])


def _validate_range(start: datetime | None, end: datetime | None) -> None:
    if start is not None and end is not None and start >= end:
        raise HTTPException(http_status.HTTP_422_UNPROCESSABLE_ENTITY, "start must be before end")


@router.post("/carbon-factors", response_model=CarbonFactorOut, status_code=201)
async def create_carbon_factor(
    body: CarbonFactorCreate,
    admin: User = Depends(require_role("admin")),
    session: AsyncSession = Depends(get_session),
):
    factor = CarbonFactor(organization_id=admin.organization_id, **body.model_dump())
    session.add(factor)
    await session.flush()
    await record_audit(
        session,
        actor_user_id=admin.id,
        action="create_carbon_factor",
        object_type="carbon_factor",
        object_id=factor.id,
        details={"name": factor.name, "version": factor.version},
    )
    await session.commit()
    await session.refresh(factor)
    return factor


@router.get("/carbon-factors", response_model=list[CarbonFactorOut])
async def list_carbon_factors(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(CarbonFactor).where(
            CarbonFactor.organization_id == user.organization_id,
            CarbonFactor.active.is_(True),
        )
    )
    return result.scalars().all()


@router.get("/rollup", response_model=RollupOut)
async def energy_rollup(
    asset_id: uuid.UUID,
    measurement_type: str,
    granularity: str,
    agg: str,
    quality: str = "valid",
    start: datetime | None = None,
    end: datetime | None = None,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    _validate_range(start, end)
    f = TelemetryFilter(user.organization_id, asset_id, measurement_type, quality, start, end)
    data = await select_rollup(session, user.organization_id, f, granularity, agg)
    return RollupOut(**data)


@router.get("/trend", response_model=Union[TrendOut, RollupOut])
async def energy_trend(
    asset_id: uuid.UUID,
    measurement_type: str,
    quality: str = "valid",
    start: datetime | None = None,
    end: datetime | None = None,
    agg: str | None = None,
    granularity: str = "hour",
    limit: int = 100,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    _validate_range(start, end)
    f = TelemetryFilter(user.organization_id, asset_id, measurement_type, quality, start, end)
    if agg is not None:
        data = await select_rollup(session, user.organization_id, f, granularity, agg)
        return RollupOut(**data)
    data = await select_trend(session, user.organization_id, f, min(limit, 1000))
    return TrendOut(**data)


@router.post("/compare", response_model=CompareOut)
async def energy_compare(
    body: CompareRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    data = await compare_assets(session, user.organization_id, body)
    return CompareOut(
        basis=data["basis"],
        assets=[CompareAssetOut(**a) for a in data["assets"]],
        flags=data["flags"],
    )


@router.get("/carbon", response_model=CarbonEstimateOut)
async def energy_carbon(
    asset_id: uuid.UUID,
    measurement_type: str,
    factor_id: uuid.UUID,
    quality: str = "valid",
    start: datetime | None = None,
    end: datetime | None = None,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    _validate_range(start, end)
    f = TelemetryFilter(user.organization_id, asset_id, measurement_type, quality, start, end)
    data = await estimate_carbon(session, user.organization_id, f, factor_id)
    return CarbonEstimateOut(**data)


@router.get("/efficiency", response_model=EfficiencyOut)
async def energy_efficiency(
    asset_id: uuid.UUID,
    input_measurement_type: str,
    output_measurement_type: str,
    quality: str = "valid",
    start: datetime | None = None,
    end: datetime | None = None,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    _validate_range(start, end)
    f = TelemetryFilter(
        user.organization_id, asset_id, input_measurement_type, quality, start, end
    )
    data = await compute_efficiency(
        session, user.organization_id, f, output_measurement_type
    )
    return EfficiencyOut(**data)
