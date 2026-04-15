import datetime as dt
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict


class BillingDetailRead(BaseModel):
    """Full billing detail including JSONB fields."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    date: dt.date
    provider: str
    data_source_id: int
    project_id: str | None
    project_name: str | None
    product: str | None
    usage_type: str | None
    region: str | None
    cost: Decimal
    usage_quantity: Decimal
    usage_unit: str | None
    currency: str
    tags: Any | None
    additional_info: Any | None


class BillingListRead(BaseModel):
    """Lightweight billing row for list views — excludes heavy JSONB columns."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    date: dt.date
    provider: str
    data_source_id: int
    project_id: str | None
    project_name: str | None
    product: str | None
    usage_type: str | None
    region: str | None
    cost: Decimal
    usage_quantity: Decimal
    usage_unit: str | None
    currency: str


class SyncRequest(BaseModel):
    start_month: str  # YYYY-MM
    end_month: str | None = None
    provider: str | None = None  # aws / gcp / azure


class SyncLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    data_source_id: int
    celery_task_id: str | None
    start_time: dt.datetime
    end_time: dt.datetime | None
    status: str | None
    query_start_date: dt.date | None
    query_end_date: dt.date | None
    records_fetched: int
    records_upserted: int
    error_message: str | None


class ResourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    provider: str
    project_id: str | None
    data_source_id: int | None
    resource_id: str | None
    resource_name: str | None
    resource_type: str | None
    product: str | None
    region: str | None
    status: str
    tags: Any | None
    monthly_cost: Decimal
    first_seen_at: dt.datetime | None
    last_seen_at: dt.datetime | None


class AlertRuleCreate(BaseModel):
    name: str
    target_type: str
    target_id: str | None = None
    threshold_type: str
    threshold_value: Decimal
    notify_webhook: str | None = None
    notify_email: str | None = None


class AlertRuleUpdate(BaseModel):
    name: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    threshold_type: str | None = None
    threshold_value: Decimal | None = None
    notify_webhook: str | None = None
    notify_email: str | None = None
    is_active: bool | None = None


class AlertRuleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    target_type: str
    target_id: str | None
    threshold_type: str
    threshold_value: Decimal
    notify_webhook: str | None
    notify_email: str | None
    is_active: bool
    created_at: dt.datetime


class AlertHistoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rule_id: int
    triggered_at: dt.datetime
    actual_value: Decimal | None
    threshold_value: Decimal | None
    message: str | None
    notified: bool


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    message: str
    type: str
    is_read: bool
    alert_history_id: int | None
    created_at: dt.datetime


class MonthlyBillRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    month: str
    category_id: int
    provider: str | None
    original_cost: Decimal
    markup_rate: Decimal
    final_cost: Decimal
    adjustment: Decimal
    status: str
    confirmed_at: dt.datetime | None
    notes: str | None
    created_at: dt.datetime


class MonthlyBillAdjust(BaseModel):
    adjustment: Decimal
    notes: str | None = None


class MonthlyBillGenerate(BaseModel):
    month: str  # YYYY-MM


class ExchangeRateCreate(BaseModel):
    date: dt.date
    from_currency: str
    to_currency: str
    rate: Decimal


class ExchangeRateUpdate(BaseModel):
    rate: Decimal


class ExchangeRateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    date: dt.date
    from_currency: str
    to_currency: str
    rate: Decimal
