"""Metering API — cloud resource usage from billing_data (AWS/GCP/Azure sync)."""

import csv
import io
import datetime as dt
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_principal
from app.auth.principal import Principal
from app.auth.scope import visible_data_source_ids
from app.database import get_db
from app.models.billing import BillingData
from app.models.project import Project
from app.models.supplier import Supplier
from app.models.supply_source import SupplySource
from app.schemas.metering import (
    UsageSummary,
    DailyUsageStats,
    ServiceUsageStats,
    UsageDetailRead,
)

router = APIRouter()


async def _principal_scope(stmt, db: AsyncSession, principal: Principal):
    """Apply data_source_id whitelist for non-admin principals."""
    visible_ds = await visible_data_source_ids(db, principal)
    if visible_ds is None:
        return stmt  # admin, full access
    if not visible_ds:
        return stmt.where(BillingData.data_source_id.in_([-1]))
    return stmt.where(BillingData.data_source_id.in_(visible_ds))

_ACCOUNT_ID_QUERY = Query(
    None,
    description="按服务账号过滤：projects 表主键 id（服务账号列表中的 id），非云厂商订阅/账号字符串",
)

_ACCOUNT_IDS_QUERY = Query(
    None,
    description="按服务账号批量过滤：projects 表主键 id 列表；与 account_id 二选一，传入时优先生效",
)


def _parse_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail=f"无效日期格式: {value}，请使用 YYYY-MM-DD")


def _apply_filters(stmt, date_start, date_end, provider, product):
    d_start = _parse_date(date_start)
    d_end = _parse_date(date_end)
    if d_start:
        stmt = stmt.where(BillingData.date >= d_start)
    if d_end:
        stmt = stmt.where(BillingData.date <= d_end)
    if provider:
        stmt = stmt.where(BillingData.provider == provider)
    if product:
        stmt = stmt.where(BillingData.product == product)
    return stmt


def _metering_scope(
    stmt,
    *,
    account_id: int | None = None,
    account_ids: list[int] | None = None,
    supply_source_id: int | None = None,
    supplier_name: str | None = None,
    data_source_id: int | None = None,
):
    """可选筛选：货源 supply_source_id、供应商名 supplier_name、data_source_id、account_id / account_ids（projects.id）。

    account_ids 非空时优先生效；否则 account_id 生效；两者都没有再落到 supply_source_id / supplier_name。
    """
    if data_source_id is not None:
        stmt = stmt.where(BillingData.data_source_id == data_source_id)
    if account_ids:
        stmt = (
            stmt.join(
                Project,
                BillingData.project_id == func.trim(Project.external_project_id),
            )
            .join(SupplySource, Project.supply_source_id == SupplySource.id)
            .where(
                BillingData.provider == SupplySource.provider,
                Project.id.in_(account_ids),
            )
        )
    elif account_id is not None:
        stmt = (
            stmt.join(
                Project,
                BillingData.project_id == func.trim(Project.external_project_id),
            )
            .join(SupplySource, Project.supply_source_id == SupplySource.id)
            .where(
                BillingData.provider == SupplySource.provider,
                Project.id == account_id,
            )
        )
    elif supply_source_id is not None:
        stmt = (
            stmt.join(
                Project,
                BillingData.project_id == func.trim(Project.external_project_id),
            )
            .join(SupplySource, Project.supply_source_id == SupplySource.id)
            .where(
                BillingData.provider == SupplySource.provider,
                SupplySource.id == supply_source_id,
            )
        )
    elif supplier_name is not None:
        stmt = (
            stmt.join(
                Project,
                BillingData.project_id == func.trim(Project.external_project_id),
            )
            .join(SupplySource, Project.supply_source_id == SupplySource.id)
            .join(Supplier, SupplySource.supplier_id == Supplier.id)
            .where(BillingData.provider == SupplySource.provider)
        )
        if supplier_name == "(未分组)":
            stmt = stmt.where(Supplier.name == "未分组")
        else:
            stmt = stmt.where(Supplier.name == supplier_name)
    return stmt


@router.get("/summary", response_model=UsageSummary)
async def metering_summary(
    date_start: str | None = None,
    date_end: str | None = None,
    provider: str | None = None,
    product: str | None = None,
    account_id: int | None = _ACCOUNT_ID_QUERY,
    account_ids: list[int] | None = _ACCOUNT_IDS_QUERY,
    supply_source_id: int | None = Query(None),
    supplier_name: str | None = Query(None),
    data_source_id: int | None = Query(None),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    stmt = _apply_filters(
        select(
            func.coalesce(func.sum(BillingData.cost), 0).label("total_cost"),
            func.coalesce(func.sum(BillingData.usage_quantity), 0).label("total_usage"),
            func.count().label("record_count"),
            func.count(func.distinct(BillingData.product)).label("service_count"),
        ).select_from(BillingData),
        date_start, date_end, provider, product,
    )
    stmt = _metering_scope(
        stmt,
        account_id=account_id,
        account_ids=account_ids,
        supply_source_id=supply_source_id,
        supplier_name=supplier_name,
        data_source_id=data_source_id,
    )
    stmt = await _principal_scope(stmt, db, principal)
    row = (await db.execute(stmt)).one()
    return UsageSummary(
        total_cost=row.total_cost,
        total_usage=row.total_usage,
        record_count=row.record_count,
        service_count=row.service_count,
    )


@router.get("/daily", response_model=list[DailyUsageStats])
async def metering_daily(
    date_start: str | None = None,
    date_end: str | None = None,
    provider: str | None = None,
    product: str | None = None,
    account_id: int | None = _ACCOUNT_ID_QUERY,
    account_ids: list[int] | None = _ACCOUNT_IDS_QUERY,
    supply_source_id: int | None = Query(None),
    supplier_name: str | None = Query(None),
    data_source_id: int | None = Query(None),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    base = (
        select(
            BillingData.date,
            func.coalesce(func.sum(BillingData.usage_quantity), 0).label("usage_quantity"),
            func.coalesce(func.sum(BillingData.cost), 0).label("cost"),
            func.count().label("record_count"),
        )
        .select_from(BillingData)
    )
    stmt = _apply_filters(base, date_start, date_end, provider, product)
    stmt = _metering_scope(
        stmt,
        account_id=account_id,
        account_ids=account_ids,
        supply_source_id=supply_source_id,
        supplier_name=supplier_name,
        data_source_id=data_source_id,
    )
    stmt = await _principal_scope(stmt, db, principal)
    stmt = stmt.group_by(BillingData.date).order_by(BillingData.date)
    rows = (await db.execute(stmt)).all()
    return [
        DailyUsageStats(
            date=r.date,
            usage_quantity=r.usage_quantity,
            cost=r.cost,
            record_count=r.record_count,
        )
        for r in rows
    ]


@router.get("/by-service", response_model=list[ServiceUsageStats])
async def metering_by_service(
    date_start: str | None = None,
    date_end: str | None = None,
    provider: str | None = None,
    account_id: int | None = _ACCOUNT_ID_QUERY,
    account_ids: list[int] | None = _ACCOUNT_IDS_QUERY,
    supply_source_id: int | None = Query(None),
    supplier_name: str | None = Query(None),
    data_source_id: int | None = Query(None),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    base = (
        select(
            BillingData.product,
            func.coalesce(func.sum(BillingData.usage_quantity), 0).label("usage_quantity"),
            func.max(BillingData.usage_unit).label("usage_unit"),
            func.coalesce(func.sum(BillingData.cost), 0).label("cost"),
            func.count().label("record_count"),
        )
        .select_from(BillingData)
    )
    stmt = _apply_filters(base, date_start, date_end, provider, None)
    stmt = _metering_scope(
        stmt,
        account_id=account_id,
        account_ids=account_ids,
        supply_source_id=supply_source_id,
        supplier_name=supplier_name,
        data_source_id=data_source_id,
    )
    stmt = await _principal_scope(stmt, db, principal)
    stmt = stmt.group_by(BillingData.product).order_by(func.sum(BillingData.usage_quantity).desc())
    rows = (await db.execute(stmt)).all()
    return [
        ServiceUsageStats(
            product=r.product or "Unknown",
            usage_quantity=r.usage_quantity,
            usage_unit=r.usage_unit,
            cost=r.cost,
            record_count=r.record_count,
        )
        for r in rows
    ]


@router.get("/products")
async def metering_product_list(
    provider: str | None = None,
    account_id: int | None = _ACCOUNT_ID_QUERY,
    account_ids: list[int] | None = _ACCOUNT_IDS_QUERY,
    supply_source_id: int | None = Query(None),
    supplier_name: str | None = Query(None),
    data_source_id: int | None = Query(None),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    stmt = (
        select(func.distinct(BillingData.product))
        .select_from(BillingData)
        .where(BillingData.product.isnot(None))
        .order_by(BillingData.product)
    )
    if provider:
        stmt = stmt.where(BillingData.provider == provider)
    stmt = _metering_scope(
        stmt,
        account_id=account_id,
        account_ids=account_ids,
        supply_source_id=supply_source_id,
        supplier_name=supplier_name,
        data_source_id=data_source_id,
    )
    stmt = await _principal_scope(stmt, db, principal)
    rows = (await db.execute(stmt)).scalars().all()
    return [{"product": p} for p in rows]


@router.get("/detail", response_model=list[UsageDetailRead])
async def metering_detail(
    date_start: str | None = None,
    date_end: str | None = None,
    provider: str | None = None,
    product: str | None = None,
    account_id: int | None = _ACCOUNT_ID_QUERY,
    account_ids: list[int] | None = _ACCOUNT_IDS_QUERY,
    supply_source_id: int | None = Query(None),
    supplier_name: str | None = Query(None),
    data_source_id: int | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    stmt = _apply_filters(
        select(
            BillingData.id,
            BillingData.date,
            BillingData.provider,
            BillingData.data_source_id,
            BillingData.project_id,
            BillingData.product,
            BillingData.usage_type,
            BillingData.region,
            BillingData.cost,
            BillingData.usage_quantity,
            BillingData.usage_unit,
            BillingData.currency,
        ),
        date_start, date_end, provider, product,
    )
    stmt = _metering_scope(
        stmt,
        account_id=account_id,
        account_ids=account_ids,
        supply_source_id=supply_source_id,
        supplier_name=supplier_name,
        data_source_id=data_source_id,
    )
    stmt = await _principal_scope(stmt, db, principal)
    stmt = (
        stmt.order_by(BillingData.date.desc(), BillingData.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await db.execute(stmt)).all()
    return rows


@router.get("/detail/count")
async def metering_detail_count(
    date_start: str | None = None,
    date_end: str | None = None,
    provider: str | None = None,
    product: str | None = None,
    account_id: int | None = _ACCOUNT_ID_QUERY,
    account_ids: list[int] | None = _ACCOUNT_IDS_QUERY,
    supply_source_id: int | None = Query(None),
    supplier_name: str | None = Query(None),
    data_source_id: int | None = Query(None),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    stmt = _apply_filters(
        select(func.count()).select_from(BillingData),
        date_start, date_end, provider, product,
    )
    stmt = _metering_scope(
        stmt,
        account_id=account_id,
        account_ids=account_ids,
        supply_source_id=supply_source_id,
        supplier_name=supplier_name,
        data_source_id=data_source_id,
    )
    stmt = await _principal_scope(stmt, db, principal)
    result = await db.execute(stmt)
    return {"total": result.scalar_one()}


_CSV_HEADER = [
    "date", "provider", "project_id", "product",
    "usage_type", "region", "cost", "usage_quantity", "usage_unit", "currency",
]


async def _stream_csv(stmt) -> AsyncIterator[str]:
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
                writer.writerow([
                    r.date.isoformat(), r.provider, r.project_id or "",
                    r.product or "", r.usage_type or "", r.region or "",
                    str(r.cost), str(r.usage_quantity), r.usage_unit or "",
                    r.currency,
                ])
                last_date = r.date
                last_id = r.id
            yield buf.getvalue()

            if len(rows) < CHUNK:
                break


@router.get("/export")
async def metering_export(
    date_start: str | None = None,
    date_end: str | None = None,
    provider: str | None = None,
    product: str | None = None,
    account_id: int | None = _ACCOUNT_ID_QUERY,
    account_ids: list[int] | None = _ACCOUNT_IDS_QUERY,
    supply_source_id: int | None = Query(None),
    supplier_name: str | None = Query(None),
    data_source_id: int | None = Query(None),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    stmt = _apply_filters(
        select(
            BillingData.id,
            BillingData.date,
            BillingData.provider,
            BillingData.project_id,
            BillingData.product,
            BillingData.usage_type,
            BillingData.region,
            BillingData.cost,
            BillingData.usage_quantity,
            BillingData.usage_unit,
            BillingData.currency,
        ),
        date_start, date_end, provider, product,
    )
    stmt = _metering_scope(
        stmt,
        account_id=account_id,
        account_ids=account_ids,
        supply_source_id=supply_source_id,
        supplier_name=supplier_name,
        data_source_id=data_source_id,
    )
    stmt = await _principal_scope(stmt, db, principal)
    return StreamingResponse(
        _stream_csv(stmt),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=metering_billing_export.csv"},
    )
