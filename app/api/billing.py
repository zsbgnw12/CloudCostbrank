"""Billing detail & export API."""

import csv
import io
import datetime as dt
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_principal
from app.auth.principal import Principal
from app.auth.scope import visible_data_source_ids
from app.database import get_db
from app.models.billing import BillingData
from app.schemas.billing import BillingListRead

router = APIRouter()


async def _scope_filter(stmt, db: AsyncSession, principal: Principal):
    """Apply data_source_id whitelist for non-admin principals."""
    visible_ds = await visible_data_source_ids(db, principal)
    if visible_ds is None:
        return stmt, True  # admin, full access
    if not visible_ds:
        return stmt.where(BillingData.data_source_id.in_([-1])), False
    return stmt.where(BillingData.data_source_id.in_(visible_ds)), True


def _parse_optional_date(value: str | None, *, param: str) -> dt.date | None:
    if value is None or value == "":
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"无效的日期参数 {param}，请使用 YYYY-MM-DD（收到: {value!r}）",
        )


_LIST_COLUMNS = [
    BillingData.id,
    BillingData.date,
    BillingData.provider,
    BillingData.data_source_id,
    BillingData.project_id,
    BillingData.project_name,
    BillingData.product,
    BillingData.usage_type,
    BillingData.region,
    BillingData.cost,
    BillingData.usage_quantity,
    BillingData.usage_unit,
    BillingData.currency,
]


def _apply_filters(stmt, date_start: dt.date | None, date_end: dt.date | None, provider, project_id, product):
    if date_start is not None:
        stmt = stmt.where(BillingData.date >= date_start)
    if date_end is not None:
        stmt = stmt.where(BillingData.date <= date_end)
    if provider:
        stmt = stmt.where(BillingData.provider == provider)
    if project_id:
        stmt = stmt.where(BillingData.project_id == project_id)
    if product:
        stmt = stmt.where(BillingData.product == product)
    return stmt


@router.get("/detail", response_model=list[BillingListRead])
async def billing_detail(
    date_start: str | None = None,
    date_end: str | None = None,
    provider: str | None = None,
    project_id: str | None = None,
    product: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    ds = _parse_optional_date(date_start, param="date_start")
    de = _parse_optional_date(date_end, param="date_end")
    stmt = _apply_filters(
        select(*_LIST_COLUMNS), ds, de, provider, project_id, product,
    )
    stmt, _ = await _scope_filter(stmt, db, principal)
    stmt = stmt.order_by(BillingData.date.desc(), BillingData.id).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    return result.all()


@router.get("/detail/count")
async def billing_detail_count(
    date_start: str | None = None,
    date_end: str | None = None,
    provider: str | None = None,
    project_id: str | None = None,
    product: str | None = None,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    """Return total row count for the current filter."""
    ds = _parse_optional_date(date_start, param="date_start")
    de = _parse_optional_date(date_end, param="date_end")
    stmt = _apply_filters(
        select(func.count()).select_from(BillingData),
        ds, de, provider, project_id, product,
    )
    stmt, _ = await _scope_filter(stmt, db, principal)
    result = await db.execute(stmt)
    return {"total": result.scalar_one()}


_EXPORT_COLUMNS = [
    BillingData.id,
    BillingData.date,
    BillingData.provider,
    BillingData.project_id,
    BillingData.project_name,
    BillingData.service_id,
    BillingData.product,
    BillingData.sku_id,
    BillingData.usage_type,
    BillingData.region,
    BillingData.resource_name,
    BillingData.cost_type,
    BillingData.usage_quantity,
    BillingData.usage_unit,
    BillingData.cost,
    BillingData.cost_at_list,
    BillingData.credits_committed,
    BillingData.credits_other,
    BillingData.credits_total,
    BillingData.currency,
]

# 列对照（保持和 BQ Excel 导出口径一致）：
#   service_id   = 服务 ID
#   sku_id       = SKU ID
#   resource_name= 资源 ID
#   cost_type    = 计费类型 (regular/tax/adjustment)
#   cost         = 费用 / 小计
#   cost_at_list = 未含入的小计（标价）
#   credits_committed = 节省计划 (CUD)
#   credits_other     = 其他节省 (SUD/Promo/FreeTier)
#   credits_total     = 节省合计 (= committed + other)
_CSV_HEADER = [
    "date", "provider", "project_id", "project_name",
    "service_id", "product",
    "sku_id", "usage_type",
    "region", "resource_name", "cost_type",
    "usage_quantity", "usage_unit",
    "cost", "cost_at_list",
    "credits_committed", "credits_other", "credits_total",
    "currency",
]


async def _stream_csv(stmt) -> AsyncIterator[str]:
    """Stream CSV rows using keyset pagination on (date DESC, id DESC).

    Uses its own session to avoid get_db lifecycle issues with StreamingResponse.
    """
    from app.database import async_session_factory

    header_buf = io.StringIO()
    csv.writer(header_buf).writerow(_CSV_HEADER)
    yield header_buf.getvalue()

    CHUNK = 2000
    last_date = None
    last_id = None

    async with async_session_factory() as db:
        while True:
            chunk_stmt = stmt
            if last_date is not None:
                chunk_stmt = chunk_stmt.where(
                    tuple_(BillingData.date, BillingData.id)
                    < tuple_(last_date, last_id)
                )
            chunk_stmt = chunk_stmt.order_by(
                BillingData.date.desc(), BillingData.id.desc(),
            ).limit(CHUNK)

            result = await db.execute(chunk_stmt)
            rows = result.all()
            if not rows:
                break

            buf = io.StringIO()
            writer = csv.writer(buf)
            for r in rows:
                # 顺序需和 _CSV_HEADER 一致，新字段允许 NULL → 空串
                writer.writerow([
                    r.date.isoformat(), r.provider, r.project_id or "", r.project_name or "",
                    r.service_id or "", r.product or "",
                    r.sku_id or "", r.usage_type or "",
                    r.region or "", r.resource_name or "", r.cost_type or "",
                    str(r.usage_quantity) if r.usage_quantity is not None else "",
                    r.usage_unit or "",
                    str(r.cost) if r.cost is not None else "",
                    str(r.cost_at_list) if r.cost_at_list is not None else "",
                    str(r.credits_committed) if r.credits_committed is not None else "",
                    str(r.credits_other) if r.credits_other is not None else "",
                    str(r.credits_total) if r.credits_total is not None else "",
                    r.currency or "",
                ])
                last_date = r.date
                last_id = r.id
            yield buf.getvalue()

            if len(rows) < CHUNK:
                break


@router.get("/export")
async def billing_export(
    date_start: str | None = None,
    date_end: str | None = None,
    provider: str | None = None,
    project_id: str | None = None,
    product: str | None = None,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    ds = _parse_optional_date(date_start, param="date_start")
    de = _parse_optional_date(date_end, param="date_end")
    stmt = _apply_filters(
        select(*_EXPORT_COLUMNS), ds, de, provider, project_id, product,
    )
    stmt, _ = await _scope_filter(stmt, db, principal)

    return StreamingResponse(
        _stream_csv(stmt),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=billing_export.csv"},
    )
