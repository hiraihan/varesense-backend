import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

from varesense.energy.models import AGGREGATIONS, GRANULARITIES

OriginLiteral = Literal["measured", "calculated", "estimated"]


class RollupBucketOut(BaseModel):
    bucket: datetime
    value: float | None
    sample_count: int
    delta: float | None = None
    origin: OriginLiteral = "calculated"


class RollupOut(BaseModel):
    asset_id: uuid.UUID
    measurement_type: str
    unit: str | None
    granularity: str
    agg: str
    buckets: list[RollupBucketOut]
    origin: OriginLiteral = "calculated"


class TrendPointOut(BaseModel):
    measured_at: datetime
    value: float
    unit: str
    quality_status: str
    origin: OriginLiteral = "measured"


class TrendOut(BaseModel):
    asset_id: uuid.UUID
    measurement_type: str
    points: list[TrendPointOut]
    origin: OriginLiteral = "measured"


class CompareRequest(BaseModel):
    asset_id_a: uuid.UUID
    asset_id_b: uuid.UUID
    measurement_type: str
    start: datetime
    end: datetime
    output_measurement_type: str | None = None

    @model_validator(mode="after")
    def _validate(self):
        if self.start >= self.end:
            raise ValueError("start must be before end")
        return self


class CompareAssetOut(BaseModel):
    asset_id: uuid.UUID
    total: float | None
    unit: str | None
    sample_count: int


class CompareOut(BaseModel):
    basis: str
    assets: list[CompareAssetOut]
    flags: list[str]


class CarbonFactorCreate(BaseModel):
    name: str
    unit: str
    factor: float
    source: str
    version: str
    basis: str
    assumptions: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _validate(self):
        if self.factor <= 0:
            raise ValueError("factor must be positive")
        if not self.unit.strip() or not self.source.strip():
            raise ValueError("unit and source are required")
        return self


class CarbonFactorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    unit: str
    factor: float
    source: str
    version: str
    basis: str
    assumptions: dict[str, Any] | None
    active: bool
    created_at: datetime


class CarbonEstimateOut(BaseModel):
    asset_id: uuid.UUID
    measurement_type: str
    estimate: float  # kg CO2e
    unit: str
    origin: OriginLiteral = "estimated"
    factor: CarbonFactorOut


class EfficiencyOut(BaseModel):
    asset_id: uuid.UUID
    input_measurement_type: str
    input_total: float | None
    input_unit: str | None
    output_measurement_type: str
    output_total: float | None
    output_unit: str | None
    intensity: float | None
    efficiency: float | None
    basis_note: str
    origin: OriginLiteral = "calculated"
