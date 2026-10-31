import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, model_validator

from varesense.alerts.models import ALERT_STATUSES, OPERATORS, SEVERITIES


def _validate_rule_domain(operator: str | None, severity: str | None) -> None:
    if operator is not None and operator not in OPERATORS:
        raise ValueError(f"operator must be one of {OPERATORS}")
    if severity is not None and severity not in SEVERITIES:
        raise ValueError(f"severity must be one of {SEVERITIES}")


class RuleCreate(BaseModel):
    name: str
    parameter: str
    operator: str
    value: float
    unit: str
    severity: str
    asset_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _validate(self):
        _validate_rule_domain(self.operator, self.severity)
        return self


class RuleUpdate(BaseModel):
    name: str | None = None
    operator: str | None = None
    value: float | None = None
    unit: str | None = None
    severity: str | None = None
    asset_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _validate(self):
        _validate_rule_domain(self.operator, self.severity)
        return self


class RuleOut(BaseModel):
    id: uuid.UUID
    rule_key: uuid.UUID
    organization_id: uuid.UUID
    version: int
    name: str
    parameter: str
    operator: str
    value: float
    unit: str
    severity: str
    asset_id: uuid.UUID | None
    active: bool
    created_at: datetime


class AlertTransition(BaseModel):
    status: str

    @model_validator(mode="after")
    def _validate(self):
        if self.status not in ALERT_STATUSES:
            raise ValueError(f"status must be one of {ALERT_STATUSES}")
        return self


class AlertOut(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    rule_id: uuid.UUID | None
    asset_id: uuid.UUID | None
    device_id: uuid.UUID | None
    parameter: str
    value: float
    unit: str
    severity: str
    source: dict[str, Any] | None
    evidence: dict[str, Any]
    status: str
    created_at: datetime
    updated_at: datetime
