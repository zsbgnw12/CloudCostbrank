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


# ─── /api/billing/export —— 已部署接口，路由名不能动 ───────────────
# 19 列契约（见下表）。删了过时的 credits_committed/credits_other（reseller 数据下永远 0），
# 替换成 credits_total —— 已对接程序看到的 cost / cost_at_list / credits_total / 等核心字段不变。
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
    BillingData.credits_total,
    BillingData.currency,
    BillingData.invoice_month,  # 给已对接程序加发票月（追加到末尾，不破坏列序）
]

_CSV_HEADER = [
    "date", "provider", "project_id", "project_name",
    "service_id", "product",
    "sku_id", "usage_type",
    "region", "resource_name", "cost_type",
    "usage_quantity", "usage_unit",
    "cost", "cost_at_list", "credits_total",
    "currency",
    "invoice_month",
]


# ─── /api/billing/export-full —— 全量导出 ─────────────────────────
# 给：(a) 外部程序定时拉取明细做二次分析；(b) 内部财务/运维对账核查。
# /export 字段 + 全量内部/分类字段。和 BQ Excel 列对齐。
_EXPORT_COLUMNS_FULL = _EXPORT_COLUMNS + [
    BillingData.data_source_id,
    BillingData.billing_account_id,
    BillingData.transaction_type,
    BillingData.seller_name,
    BillingData.currency_conversion_rate,
    BillingData.consumption_model_id,
    BillingData.consumption_model_description,
    BillingData.credits_breakdown,
    BillingData.tags,
    BillingData.system_labels,
    BillingData.additional_info,
]

_CSV_HEADER_FULL = _CSV_HEADER + [
    "data_source_id",
    "billing_account_id",
    "transaction_type", "seller_name",
    "currency_conversion_rate",
    "consumption_model_id", "consumption_model_description",
    "credits_breakdown",
    "tags", "system_labels", "additional_info",
]


def _csv_str(v) -> str:
    """Format a value for CSV. None -> '', Decimal/dict -> str()."""
    if v is None:
        return ""
    return str(v) if not isinstance(v, str) else v


async def _stream_csv(stmt) -> AsyncIterator[str]:
    """19 列基础 CSV — /api/billing/export 路由用。"""
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

            rows = (await db.execute(chunk_stmt)).all()
            if not rows:
                break

            buf = io.StringIO()
            writer = csv.writer(buf)
            for r in rows:
                # 顺序必须和 _CSV_HEADER 严格一致
                writer.writerow([
                    r.date.isoformat(), r.provider, r.project_id or "", r.project_name or "",
                    r.service_id or "", r.product or "",
                    r.sku_id or "", r.usage_type or "",
                    r.region or "", r.resource_name or "", r.cost_type or "",
                    _csv_str(r.usage_quantity), r.usage_unit or "",
                    _csv_str(r.cost), _csv_str(r.cost_at_list), _csv_str(r.credits_total),
                    r.currency or "",
                    r.invoice_month or "",
                ])
                last_date = r.date
                last_id = r.id
            yield buf.getvalue()
            if len(rows) < CHUNK:
                break


async def _stream_csv_full(stmt) -> AsyncIterator[str]:
    """全量 CSV —— 在 19 列基础上追加完整业务字段 + JSONB 扩展。"""
    from app.database import async_session_factory
    import json as _json

    header_buf = io.StringIO()
    csv.writer(header_buf).writerow(_CSV_HEADER_FULL)
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

            rows = (await db.execute(chunk_stmt)).all()
            if not rows:
                break

            buf = io.StringIO()
            writer = csv.writer(buf)
            for r in rows:
                writer.writerow([
                    r.date.isoformat(), r.provider, r.project_id or "", r.project_name or "",
                    r.service_id or "", r.product or "",
                    r.sku_id or "", r.usage_type or "",
                    r.region or "", r.resource_name or "", r.cost_type or "",
                    _csv_str(r.usage_quantity), r.usage_unit or "",
                    _csv_str(r.cost), _csv_str(r.cost_at_list), _csv_str(r.credits_total),
                    r.currency or "",
                    r.invoice_month or "",
                    # 全量追加列
                    r.data_source_id,
                    r.billing_account_id or "",
                    r.transaction_type or "",
                    r.seller_name or "",
                    _csv_str(r.currency_conversion_rate),
                    r.consumption_model_id or "",
                    r.consumption_model_description or "",
                    _json.dumps(r.credits_breakdown, ensure_ascii=False) if r.credits_breakdown else "",
                    _json.dumps(r.tags, ensure_ascii=False) if r.tags else "",
                    _json.dumps(r.system_labels, ensure_ascii=False) if r.system_labels else "",
                    _json.dumps(r.additional_info, ensure_ascii=False) if r.additional_info else "",
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
    """已部署接口 —— 19 列；不要改路由名也不要减列，已有程序对接。"""
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


@router.get("/export-full")
async def billing_export_full(
    date_start: str | None = None,
    date_end: str | None = None,
    provider: str | None = None,
    project_id: str | None = None,
    product: str | None = None,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    """全量导出 —— BillingData 表所有面向业务的字段，给程序对接 + 内部对账用。

    新加列见 _CSV_HEADER_FULL。和 BQ Excel 导出口径对齐：
      cost_at_list = 未含入的小计 (标价)
      credits_committed = 节省计划 (CUD)
      credits_other = 其他节省
      credits_total = 节省合计
    """
    ds = _parse_optional_date(date_start, param="date_start")
    de = _parse_optional_date(date_end, param="date_end")
    stmt = _apply_filters(
        select(*_EXPORT_COLUMNS_FULL), ds, de, provider, project_id, product,
    )
    stmt, _ = await _scope_filter(stmt, db, principal)

    return StreamingResponse(
        _stream_csv_full(stmt),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=billing_export_full.csv"},
    )
