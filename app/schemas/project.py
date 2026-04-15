from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ProjectCreate(BaseModel):
    name: str
    supply_source_id: int
    external_project_id: str
    data_source_id: int | None = None
    category_id: int | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    data_source_id: int | None = None
    category_id: int | None = None
    notes: str | None = None


class ProjectRead(BaseModel):
    """展示用：provider / supplier_name 来自 supply_sources + suppliers，非 projects 列。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    supply_source_id: int
    provider: str
    supplier_name: str
    external_project_id: str
    data_source_id: int | None
    category_id: int | None
    status: str
    notes: str | None
    created_at: datetime
    updated_at: datetime


class ProjectAssignmentLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    action: str
    from_status: str | None
    to_status: str | None
    operator: str | None
    notes: str | None
    created_at: datetime
