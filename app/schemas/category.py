from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class CategoryCreate(BaseModel):
    name: str
    markup_rate: Decimal = Decimal("1.0")
    description: str | None = None


class CategoryUpdate(BaseModel):
    name: str | None = None
    markup_rate: Decimal | None = None
    description: str | None = None


class CategoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    markup_rate: Decimal
    description: str | None
    created_at: datetime
    updated_at: datetime
