import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    email: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class UserCreate(BaseModel):
    email: str
    password: str
    role: Literal["admin", "operator"] = "operator"


class BootstrapRequest(BaseModel):
    org_name: str
    email: str
    password: str


class UserOut(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    email: str
    role: str
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
