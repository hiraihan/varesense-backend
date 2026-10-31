import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, model_validator

from varesense.analytics.models import ANOMALY_STATUSES


class TrendSampleOut(BaseModel):
    measured_at: datetime
    value: float
    unit: str
    quality_status: str
    sensor_id: uuid.UUID


class TrendSummaryOut(BaseModel):
    count: int
    min: float | None
    max: float | None
    mean: float | None


class TrendOut(BaseModel):
    samples: list[TrendSampleOut]
    summary: TrendSummaryOut


class BaselineCreate(BaseModel):
    asset_id: uuid.UUID
    measurement_type: str
    placement: str | None = None
    period_start: datetime
    period_end: datetime
    operating_context: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _validate(self):
        if not self.measurement_type.strip():
            raise ValueError("measurement_type is required")
        if self.period_start >= self.period_end:
            raise ValueError("period_start must be before period_end")
        return self


class BaselineOut(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    asset_id: uuid.UUID
    measurement_type: str
    placement: str | None
    period_start: datetime
    period_end: datetime
    operating_context: dict[str, Any] | None
    n: int
    mean: float
    std: float
    min_value: float
    max_value: float
    training_until: datetime
    method_version: str
    active: bool
    created_at: datetime


class AnomalyUpdate(BaseModel):
    status: str

    @model_validator(mode="after")
    def _validate(self):
        if self.status not in ANOMALY_STATUSES:
            raise ValueError(f"status must be one of {ANOMALY_STATUSES}")
        return self


class AnomalyOut(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    baseline_id: uuid.UUID
    asset_id: uuid.UUID
    device_id: uuid.UUID | None
    measurement_type: str
    measured_at: datetime
    value: float
    z_score: float
    gradual: bool
    status: str
    source: dict[str, Any] | None
    evidence: dict[str, Any] | None
    created_at: datetime
