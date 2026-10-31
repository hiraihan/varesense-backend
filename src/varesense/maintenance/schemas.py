import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, model_validator

from varesense.maintenance.models import MAINTENANCE_KINDS, MAINTENANCE_OUTCOMES


class MaintenanceCreate(BaseModel):
    asset_id: uuid.UUID
    alert_id: uuid.UUID | None = None
    kind: str
    findings: dict[str, Any] | None = None
    outcome: str

    @model_validator(mode="after")
    def _validate(self):
        if self.kind not in MAINTENANCE_KINDS:
            raise ValueError(f"kind must be one of {MAINTENANCE_KINDS}")
        if self.outcome not in MAINTENANCE_OUTCOMES:
            raise ValueError(f"outcome must be one of {MAINTENANCE_OUTCOMES}")
        return self


class MaintenanceOut(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    asset_id: uuid.UUID
    alert_id: uuid.UUID | None
    kind: str
    findings: dict[str, Any] | None
    outcome: str
    author_user_id: uuid.UUID
    created_at: datetime
