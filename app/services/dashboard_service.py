"""Dashboard aggregation service with Redis caching.

Reads from pre-aggregated billing_daily_summary for most queries.
Falls back to billing_data only when summary columns are insufficient
(region breakdown, unassigned project names).
"""

import datetime as dt
import json
import hashlib
import logging
from decimal import Decimal

from sqlalchemy import func, case, text, literal_column
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.billing import BillingData
from app.models.daily_summary import BillingDailySummary
from app.models.project import Project
from app.models.data_source import DataSource
from app.models.category import Category
from app.models.supply_source import SupplySource

import redis.asyncio as aioredis
from app.config import settings

logger = logging.getLogger(__name__)

_redis: aioredis.Redis | None = None
CACHE_TTL = 300


async def _get_redis() -> aioredis.Redis | None:
    """Get Redis connection with automatic reconnection on failure."""
    global _redis
    if _redis is not None:
        try:
            await _redis.ping()
            return _redis
        except Exception:
            try:
                await _redis.aclose()
            except Exception:
                pass
            _redis = None
    try:
        url = settings.REDIS_URL.split("?")[0]
        _redis = aioredis.from_url(url, decode_responses=True, ssl_cert_reqs="none")
        await _redis.ping()
    except Exception:
        _redis = None
    return _redis


async def _cache_get(key: str):
    r = await _get_redis()
    if not r:
        return None
    try:
        val = await r.get(key)
        if val:
            return json.loads(val)
    except Exception:
        pass
    return None


def _json_default(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    return str(obj)


async def _cache_set(key: str, data, ttl: int = CACHE_TTL):
    r = await _get_redis()
    if not r:
        return
    try:
        await r.set(key, json.dumps(data, default=_json_default), ex=ttl)
    except Exception:
        pass


def _cache_key(*parts) -> str:
    raw = ":".join(str(p) for p in parts)
    return f"dashboard:{hashlib.md5(raw.encode()).hexdigest()}"


def _month_range(month: str):
    """Return (start_date, end_date) for a YYYY-MM string."""
    year, mon = map(int, month.split("-"))
    start = dt.date(year, mon, 1)
    end = dt.date(year + (1 if mon == 12 else 0), (mon % 12) + 1, 1)
    return start, end


DS = BillingDailySummary


async def get_overview(db: AsyncSession, month: str) -> dict:
    """Monthly overview using pre-aggregated summary table."""
    ck = _cache_key("overview", month)
    cached = await _cache_get(ck)
    if cached:
        return cached

    start, end = _month_range(month)
    year, mon = start.year, start.month
    prev_start = dt.date(year - 1, 12, 1) if mon == 1 else dt.date(year, mon - 1, 1)

    res = await db.execute(
        select(
            func.coalesce(func.sum(
                case((DS.date >= start, DS.total_cost), else_=literal_column("0"))
            ), 0).label("total_cost"),
            func.coalesce(func.sum(
                case((DS.date < start, DS.total_cost), else_=literal_column("0"))
            ), 0).label("prev_cost"),
        ).where(DS.date >= prev_start, DS.date < end)
    )
    row = res.one()
    total_cost = row.total_cost
    prev_month_cost = row.prev_cost

    mom = float((total_cost - prev_month_cost) / prev_month_cost * 100) \
        if prev_month_cost and prev_month_cost > 0 else 0.0

    res_p = await db.execute(
        select(func.count()).select_from(Project).where(Project.status == "active")
    )
    active_projects = res_p.scalar_one()

    result = {
        "total_cost": total_cost,
        "prev_month_cost": prev_month_cost,
        "mom_change_pct": round(mom, 2),
        "active_projects": active_projects,
    }
    await _cache_set(ck, result)
    return result


async def get_trend(db: AsyncSession, start: str, end: str, granularity: str) -> list[dict]:
    """Cost trend with provider breakdown — reads summary table."""
    ck = _cache_key("trend", start, end, granularity)
    cached = await _cache_get(ck)
    if cached:
        return cached

    start_date = dt.date.fromisoformat(f"{start}-01")
    y, m = map(int, end.split("-"))
    end_date = dt.date(y + (1 if m == 12 else 0), (m % 12) + 1, 1)

    if granularity == "daily":
        date_expr = DS.date
    elif granularity == "weekly":
        date_expr = func.date_trunc("week", DS.date)
    else:
        date_expr = func.date_trunc("month", DS.date)

    stmt = (
        select(
            date_expr.label("period"),
            DS.provider,
            func.sum(DS.total_cost).label("cost"),
        )
        .where(DS.date >= start_date, DS.date < end_date)
        .group_by("period", DS.provider)
        .order_by("period")
    )
    res = await db.execute(stmt)
    rows = res.all()

    periods: dict[str, dict] = {}
    for period, provider, cost in rows:
        key = str(period)[:10]
        if key not in periods:
            periods[key] = {"date": key, "cost": Decimal("0"), "cost_by_provider": {}}
        periods[key]["cost"] += cost
        periods[key]["cost_by_provider"][provider] = cost

    result = list(periods.values())
    await _cache_set(ck, result)
    return result


async def get_by_provider(db: AsyncSession, month: str) -> list[dict]:
    ck = _cache_key("by_provider", month)
    cached = await _cache_get(ck)
    if cached:
        return cached

    start, end = _month_range(month)
    stmt = (
        select(DS.provider, func.sum(DS.total_cost).label("cost"))
        .where(DS.date >= start, DS.date < end)
        .group_by(DS.provider)
    )
    res = await db.execute(stmt)
    rows = res.all()
    total = sum(r.cost for r in rows) or Decimal("1")
    result = [
        {"provider": r.provider, "cost": r.cost, "percentage": round(float(r.cost / total * 100), 2)}
        for r in rows
    ]
    await _cache_set(ck, result)
    return result


async def get_by_category(db: AsyncSession, month: str) -> list[dict]:
    ck = _cache_key("by_category", month)
    cached = await _cache_get(ck)
    if cached:
        return cached

    start, end = _month_range(month)
    stmt = (
        select(
            Category.id,
            Category.name,
            func.sum(DS.total_cost).label("original_cost"),
            Category.markup_rate,
        )
        .join(DataSource, DS.data_source_id == DataSource.id)
        .join(Category, DataSource.category_id == Category.id)
        .where(DS.date >= start, DS.date < end)
        .group_by(Category.id, Category.name, Category.markup_rate)
        .order_by(text("original_cost DESC"))
    )
    res = await db.execute(stmt)
    result = [
        {
            "category_id": r.id,
            "name": r.name,
            "original_cost": r.original_cost,
            "markup_rate": r.markup_rate,
            "final_cost": r.original_cost * r.markup_rate,
        }
        for r in res.all()
    ]
    await _cache_set(ck, result)
    return result


async def get_by_project(db: AsyncSession, month: str, limit: int = 20) -> list[dict]:
    ck = _cache_key("by_project", month, limit)
    cached = await _cache_get(ck)
    if cached:
        return cached

    start, end = _month_range(month)
    stmt = (
        select(
            DS.project_id,
            func.coalesce(func.max(Project.name), func.max(DS.project_id)).label("name"),
            func.max(DS.provider).label("provider"),
            func.sum(DS.total_cost).label("cost"),
        )
        .outerjoin(
            Project,
            DS.project_id == Project.external_project_id,
        )
        .outerjoin(
            SupplySource,
            (Project.supply_source_id == SupplySource.id)
            & (DS.provider == SupplySource.provider),
        )
        .where(DS.date >= start, DS.date < end)
        .group_by(DS.project_id)
        .order_by(text("cost DESC"))
        .limit(limit)
    )
    res = await db.execute(stmt)
    result = [
        {"project_id": r.project_id, "name": r.name, "provider": r.provider, "cost": r.cost}
        for r in res.all()
    ]
    await _cache_set(ck, result)
    return result


async def get_by_service(db: AsyncSession, month: str, provider: str | None, limit: int = 20) -> list[dict]:
    ck = _cache_key("by_service", month, provider, limit)
    cached = await _cache_get(ck)
    if cached:
        return cached

    start, end = _month_range(month)

    # Get overall total first for accurate percentage calculation
    total_stmt = select(func.sum(DS.total_cost)).where(DS.date >= start, DS.date < end)
    if provider:
        total_stmt = total_stmt.where(DS.provider == provider)
    total_res = await db.execute(total_stmt)
    overall_total = total_res.scalar() or Decimal("1")

    stmt = (
        select(DS.product, func.sum(DS.total_cost).label("cost"))
        .where(DS.date >= start, DS.date < end)
    )
    if provider:
        stmt = stmt.where(DS.provider == provider)
    stmt = stmt.group_by(DS.product).order_by(text("cost DESC")).limit(limit)

    res = await db.execute(stmt)
    rows = res.all()
    result = [
        {"product": r.product, "cost": r.cost, "percentage": round(float(r.cost / overall_total * 100), 2)}
        for r in rows
    ]
    await _cache_set(ck, result)
    return result


async def get_by_region(db: AsyncSession, month: str) -> list[dict]:
    """Region breakdown — must query billing_data (summary lacks region column)."""
    ck = _cache_key("by_region", month)
    cached = await _cache_get(ck)
    if cached:
        return cached

    start, end = _month_range(month)
    stmt = (
        select(BillingData.region, BillingData.provider, func.sum(BillingData.cost).label("cost"))
        .where(
            BillingData.date >= start, BillingData.date < end,
            BillingData.region.isnot(None), BillingData.region != "",
        )
        .group_by(BillingData.region, BillingData.provider)
        .order_by(text("cost DESC"))
    )
    res = await db.execute(stmt)
    result = [{"region": r.region, "provider": r.provider, "cost": r.cost} for r in res.all()]
    await _cache_set(ck, result)
    return result


async def get_top_growth(db: AsyncSession, period: str = "7d", limit: int = 10) -> list[dict]:
    """Top-growth projects using summary table, with project name resolution."""
    ck = _cache_key("top_growth", period, limit)
    cached = await _cache_get(ck)
    if cached:
        return cached

    days = int(period.replace("d", "")) if "d" in period else 7
    today = dt.date.today()
    current_start = today - dt.timedelta(days=days)
    prev_start = current_start - dt.timedelta(days=days)

    stmt = (
        select(
            DS.project_id,
            func.coalesce(func.max(Project.name), func.max(DS.project_id)).label("name"),
            func.sum(
                case((DS.date >= current_start, DS.total_cost), else_=literal_column("0"))
            ).label("cur_cost"),
            func.sum(
                case(
                    (
                        (DS.date >= prev_start) & (DS.date < current_start),
                        DS.total_cost,
                    ),
                    else_=literal_column("0"),
                )
            ).label("prev_cost"),
        )
        .outerjoin(
            Project,
            DS.project_id == Project.external_project_id,
        )
        .outerjoin(
            SupplySource,
            (Project.supply_source_id == SupplySource.id)
            & (DS.provider == SupplySource.provider),
        )
        .where(DS.date >= prev_start)
        .group_by(DS.project_id)
        .having(func.sum(
            case((DS.date >= current_start, DS.total_cost), else_=literal_column("0"))
        ) > 1)
    )
    res = await db.execute(stmt)

    results = []
    for r in res.all():
        cur_cost = r.cur_cost
        prev_cost = r.prev_cost
        growth = float((cur_cost - prev_cost) / prev_cost * 100) if prev_cost > 0 else 999.9
        results.append({
            "project_id": r.project_id,
            "name": r.name,
            "current_cost": cur_cost,
            "previous_cost": prev_cost,
            "growth_pct": round(growth, 1),
        })

    results.sort(key=lambda x: x["growth_pct"], reverse=True)
    result = results[:limit]
    await _cache_set(ck, result)
    return result


async def get_unassigned(db: AsyncSession, month: str) -> list[dict]:
    """Unassigned projects — uses billing_data for project_name availability."""
    ck = _cache_key("unassigned", month)
    cached = await _cache_get(ck)
    if cached:
        return cached

    start, end = _month_range(month)
    stmt = (
        select(
            BillingData.project_id,
            func.max(BillingData.project_name).label("name"),
            func.max(BillingData.provider).label("provider"),
            func.sum(BillingData.cost).label("cost"),
        )
        .outerjoin(
            Project,
            BillingData.project_id == Project.external_project_id,
        )
        .outerjoin(
            SupplySource,
            (Project.supply_source_id == SupplySource.id)
            & (BillingData.provider == SupplySource.provider),
        )
        .where(
            BillingData.date >= start,
            BillingData.date < end,
            (Project.status.in_(("inactive", "standby"))) | (Project.id.is_(None)),
        )
        .group_by(BillingData.project_id)
        .order_by(text("cost DESC"))
    )
    res = await db.execute(stmt)
    result = [
        {"project_id": r.project_id, "name": r.name, "provider": r.provider, "cost": r.cost, "status": None}
        for r in res.all()
    ]
    await _cache_set(ck, result)
    return result
