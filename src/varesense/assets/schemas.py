import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, model_validator

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


class AssetUpdate(BaseModel):
    type: str | None = None
    name: str | None = None
    code: str | None = None
    parent_id: uuid.UUID | None = None
    data: dict[str, Any] | None = None
    archived: bool | None = None

    @model_validator(mode="after")
    def _validate_type(self):
        if self.type is not None and self.type not in ASSET_TYPES:
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
    archived: bool
    created_at: datetime
    updated_at: datetime
