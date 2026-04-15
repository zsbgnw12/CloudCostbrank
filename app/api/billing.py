"""Billing detail & export API."""

import csv
import io
import datetime as dt
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.billing import BillingData
from app.schemas.billing import BillingListRead

router = APIRouter()


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
):
    ds = _parse_optional_date(date_start, param="date_start")
    de = _parse_optional_date(date_end, param="date_end")
    stmt = _apply_filters(
        select(*_LIST_COLUMNS), ds, de, provider, project_id, product,
    )
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
):
    """Return total row count for the current filter."""
    ds = _parse_optional_date(date_start, param="date_start")
    de = _parse_optional_date(date_end, param="date_end")
    stmt = _apply_filters(
        select(func.count()).select_from(BillingData),
        ds, de, provider, project_id, product,
    )
    result = await db.execute(stmt)
    return {"total": result.scalar_one()}


_EXPORT_COLUMNS = [
    BillingData.id,
    BillingData.date,
    BillingData.provider,
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

_CSV_HEADER = [
    "date", "provider", "project_id", "project_name", "product",
    "usage_type", "region", "cost", "usage_quantity", "usage_unit", "currency",
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
                writer.writerow([
                    r.date.isoformat(), r.provider, r.project_id, r.project_name, r.product,
                    r.usage_type, r.region, str(r.cost), str(r.usage_quantity), r.usage_unit, r.currency,
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
):
    ds = _parse_optional_date(date_start, param="date_start")
    de = _parse_optional_date(date_end, param="date_end")
    stmt = _apply_filters(
        select(*_EXPORT_COLUMNS), ds, de, provider, project_id, product,
    )

    return StreamingResponse(
        _stream_csv(stmt),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=billing_export.csv"},
    )
