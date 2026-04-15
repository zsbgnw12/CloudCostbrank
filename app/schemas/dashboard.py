from decimal import Decimal

from pydantic import BaseModel


class DashboardOverview(BaseModel):
    total_cost: Decimal
    prev_month_cost: Decimal
    mom_change_pct: float
    active_projects: int


class TrendItem(BaseModel):
    date: str
    cost: Decimal
    cost_by_provider: dict[str, Decimal]


class ProviderCost(BaseModel):
    provider: str
    cost: Decimal
    percentage: float


class CategoryCost(BaseModel):
    category_id: int
    name: str
    original_cost: Decimal
    markup_rate: Decimal
    final_cost: Decimal


class ProjectCost(BaseModel):
    project_id: str
    name: str | None
    provider: str
    cost: Decimal


class ServiceCost(BaseModel):
    product: str
    cost: Decimal
    percentage: float


class RegionCost(BaseModel):
    region: str
    provider: str
    cost: Decimal


class TopGrowth(BaseModel):
    project_id: str
    name: str | None
    current_cost: Decimal
    previous_cost: Decimal
    growth_pct: float


class UnassignedProject(BaseModel):
    project_id: str
    name: str | None
    provider: str
    cost: Decimal
    status: str | None
