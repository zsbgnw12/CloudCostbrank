from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class DataSourceCreate(BaseModel):
    name: str
    cloud_account_id: int
    category_id: int | None = None
    config: dict[str, Any]


class DataSourceUpdate(BaseModel):
    name: str | None = None
    cloud_account_id: int | None = None
    category_id: int | None = None
    config: dict[str, Any] | None = None
    is_active: bool | None = None


class DataSourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    cloud_account_id: int
    category_id: int | None
    config: dict[str, Any]
    last_sync_at: datetime | None
    sync_status: str
    is_active: bool
    created_at: datetime
