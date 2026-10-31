import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from varesense.assets.models import ASSET_TYPES


class AssetCreate(BaseModel):
    type: str
    name: str
    code: str | None = None
    parent_id: uuid.UUID | None = None
    data: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _validate_type(self):
        if self.type not in ASSET_TYPES:
            raise ValueError(f"type must be one of {ASSET_TYPES}")
        return self


class AssetOut(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    type: str
    name: str
    code: str | None
    parent_id: uuid.UUID | None
    data: dict[str, Any] | None
    created_at: datetime
