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

    # 来自所属 CloudAccount。数据源本身不存 provider（避免与 cloud_account 重复
    # 而后不一致），但调用方要按云类型筛选数据源时，唯一的途径是再查一次
    # cloud_accounts —— 列表接口顺手带上,省掉这次往返。
    provider: str | None = None
