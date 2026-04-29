"""Pydantic schemas for metering API — reads from billing_summary (cloud sync)."""

import datetime as dt
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class UsageSummary(BaseModel):
    total_cost: Decimal
    total_usage: Decimal
    record_count: int
    service_count: int


class DailyUsageStats(BaseModel):
    date: dt.date
    usage_quantity: Decimal
    cost: Decimal
    record_count: int


class ServiceUsageStats(BaseModel):
    product: str
    usage_quantity: Decimal
    usage_unit: str | None
    cost: Decimal
    record_count: int


class UsageDetailRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    date: dt.date
    provider: str
    data_source_id: int
    project_id: str | None
    product: str | None
    usage_type: str | None
    region: str | None
    cost: Decimal
    usage_quantity: Decimal
    usage_unit: str | None
    currency: str
