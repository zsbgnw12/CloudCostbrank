"""Monthly bill generation and management service."""

import datetime as dt
from decimal import Decimal

from sqlalchemy import func, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.billing import BillingData
from app.models.data_source import DataSource
from app.models.project import Project
from app.models.category import Category
from app.models.monthly_bill import MonthlyBill


async def generate_bills(db: AsyncSession, month: str) -> int:
    """Generate monthly bills.

    Only creates new draft bills or updates existing drafts.
    Confirmed/paid bills are never touched to preserve financial integrity.
    """
    year, mon = map(int, month.split("-"))
    start = dt.date(year, mon, 1)
    end = dt.date(year + (1 if mon == 12 else 0), (mon % 12) + 1, 1)

    # 账单基数用 cost_usd(内部统一口径,永不混币)。老行未回填时 COALESCE 回退 cost
    # (现存全 USD,安全)。注:monthly_bills 暂无 currency 列,出账仍以 USD 计;
    # 若要按客户币种(CNY)出账,需给 monthly_bills 加 currency 列 + 用 cost_cny,属后续增强。
    stmt = (
        select(
            DataSource.category_id,
            BillingData.provider,
            func.sum(func.coalesce(BillingData.cost_usd, BillingData.cost)).label("original_cost"),
            Category.markup_rate,
        )
        .join(DataSource, BillingData.data_source_id == DataSource.id)
        .join(Category, DataSource.category_id == Category.id)
        .where(
            BillingData.date >= start,
            BillingData.date < end,
            DataSource.category_id.isnot(None),
        )
        .group_by(DataSource.category_id, BillingData.provider, Category.markup_rate)
    )

    res = await db.execute(stmt)
    rows = res.all()

    if not rows:
        return 0

    non_draft_result = await db.execute(
        select(MonthlyBill.category_id, MonthlyBill.provider)
        .where(
            MonthlyBill.month == month,
            MonthlyBill.status != "draft",
        )
    )
    locked_keys = {(r.category_id, r.provider) for r in non_draft_result}

    values = []
    for r in rows:
        if (r.category_id, r.provider) in locked_keys:
            continue
        final_cost = r.original_cost * r.markup_rate
        values.append({
            "month": month,
            "category_id": r.category_id,
            "provider": r.provider,
            "original_cost": r.original_cost,
            "markup_rate": r.markup_rate,
            "final_cost": final_cost,
        })

    if not values:
        return 0

    insert_stmt = pg_insert(MonthlyBill).values(values)
    upsert_stmt = insert_stmt.on_conflict_do_update(
        constraint="uq_monthly_bill",
        set_={
            "original_cost": insert_stmt.excluded.original_cost,
            "markup_rate": insert_stmt.excluded.markup_rate,
            "final_cost": insert_stmt.excluded.original_cost * insert_stmt.excluded.markup_rate + MonthlyBill.adjustment,
        },
    )
    await db.execute(upsert_stmt)
    await db.flush()
    return len(values)
