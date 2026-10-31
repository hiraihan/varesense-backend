import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, model_validator

from varesense.devices.models import (
    DEVICE_STATUSES,
    EDGE_LIFECYCLE_STATES,
    ENDPOINT_KINDS,
)


class EdgeNodeCreate(BaseModel):
    name: str
    code: str | None = None
    data: dict[str, Any] | None = None


class EdgeNodeUpdate(BaseModel):
    name: str | None = None
    code: str | None = None
    lifecycle_state: str | None = None
    device_status: str | None = None
    data: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _validate(self):
        if (
            self.lifecycle_state is not None
            and self.lifecycle_state not in EDGE_LIFECYCLE_STATES
        ):
            raise ValueError(
                f"lifecycle_state must be one of {EDGE_LIFECYCLE_STATES}"
            )
        if self.device_status is not None and self.device_status not in DEVICE_STATUSES:
            raise ValueError(f"device_status must be one of {DEVICE_STATUSES}")
        return self


class EdgeNodeOut(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    code: str | None
    lifecycle_state: str
    device_status: str
    data: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class CredentialIssue(BaseModel):
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def _validate_expiry(self):
        if self.expires_at is not None and self.expires_at <= datetime.now(timezone.utc):
            raise ValueError("expires_at must be in the future")
        return self


class CredentialOut(BaseModel):
    id: uuid.UUID
    edge_node_id: uuid.UUID
    client_id: str
    secret: str | None = None  # only present on issue/rotate response
    issued_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None


class EndpointCreate(BaseModel):
    kind: str
    name: str
    code: str | None = None
    asset_id: uuid.UUID
    data: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _validate_kind(self):
        if self.kind not in ENDPOINT_KINDS:
            raise ValueError(f"kind must be one of {ENDPOINT_KINDS}")
        return self


class EndpointOut(BaseModel):
    id: uuid.UUID
    edge_node_id: uuid.UUID
    kind: str
    name: str
    code: str | None
    asset_id: uuid.UUID
    data: dict[str, Any] | None
    created_at: datetime
