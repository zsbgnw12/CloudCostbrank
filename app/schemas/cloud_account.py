from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class CloudAccountCreate(BaseModel):
    name: str
    provider: str  # aws / gcp / azure
    secret_data: dict[str, Any]  # plaintext; will be encrypted before storage


class CloudAccountUpdate(BaseModel):
    name: str | None = None
    provider: str | None = None
    secret_data: dict[str, Any] | None = None
    is_active: bool | None = None


class CloudAccountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    provider: str
    is_active: bool
    auth_mode: str = "legacy"
    consent_status: str = "granted"
    created_at: datetime
    updated_at: datetime
    # secret_data is never returned
