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
from app.auth.principal import AuthMethod, Principal
from app.auth.scope import visible_data_source_ids
from app.database import get_db
from app.models.billing import BillingData
from app.models.cloud_account import CloudAccount
from app.models.data_source import DataSource
from app.models.project import Project
from app.models.supplier import Supplier
from app.models.supply_source import SupplySource
from app.schemas.metering import (
    UsageSummary,
    DailyUsageStats,
    ServiceUsageStats,
    UsageDetailRead,
)
from app.schemas.taiji import (
    TaijiIngestRequest,
    TaijiIngestResponse,
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

_PRODUCTS_QUERY = Query(
    None,
    description="按服务名批量过滤：billing_data.product 列；与 product 二选一，传入时优先生效",
)


def _parse_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail=f"无效日期格式: {value}，请使用 YYYY-MM-DD")


def _apply_filters(stmt, date_start, date_end, provider, product, products=None):
    d_start = _parse_date(date_start)
    d_end = _parse_date(date_end)
    if d_start:
        stmt = stmt.where(BillingData.date >= d_start)
    if d_end:
        stmt = stmt.where(BillingData.date <= d_end)
    if provider:
        stmt = stmt.where(BillingData.provider == provider)
    # products 非空时优先（多选）；否则回退到单值 product
    if products:
        stmt = stmt.where(BillingData.product.in_(products))
    elif product:
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
    products: list[str] | None = _PRODUCTS_QUERY,
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
        date_start, date_end, provider, product, products=products,
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
    products: list[str] | None = _PRODUCTS_QUERY,
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
    stmt = _apply_filters(base, date_start, date_end, provider, product, products=products)
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
    products: list[str] | None = _PRODUCTS_QUERY,
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
    stmt = _apply_filters(base, date_start, date_end, provider, None, products=products)
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
    products: list[str] | None = _PRODUCTS_QUERY,
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
        date_start, date_end, provider, product, products=products,
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
    products: list[str] | None = _PRODUCTS_QUERY,
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
        date_start, date_end, provider, product, products=products,
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
    products: list[str] | None = _PRODUCTS_QUERY,
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
        date_start, date_end, provider, product, products=products,
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


# ────────────────────── Taiji Push ingest ──────────────────────

@router.post("/taiji/ingest", response_model=TaijiIngestResponse)
async def taiji_ingest(
    body: TaijiIngestRequest,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    """
    接收 Taiji 系统 Push 过来的原始请求日志。

    鉴权约束（防止误用/越权）：
      1. 必须走 API Key（X-API-Key），不允许浏览器 session / Casdoor Bearer
      2. API Key 必须设置 allowed_cloud_account_ids，且仅含 1 个
      3. 该 CloudAccount 的 provider 必须是 "taiji"
      4. 该 CloudAccount 下必须有且仅有 1 个 DataSource（配置不歧义）

    处理流程：
      1. 原始日志按 (data_source_id, id) 主键幂等 upsert → taiji_log_raw
      2. 识别本批涉及的日期（created_at 派生 UTC 日期）
      3. 对每个涉及日期，**从 taiji_log_raw 重新全量聚合**覆盖 billing_data + token_usage
      4. 自动建新 token 的 Project
    """
    # 1. 鉴权方式必须是 API Key
    if principal.method != AuthMethod.API_KEY:
        raise HTTPException(
            status_code=403,
            detail="taiji ingest 必须使用 X-API-Key 鉴权",
        )

    # 2. API Key 必须限定单个 CloudAccount
    restricted = principal.restricted_cloud_account_ids or []
    if len(restricted) != 1:
        raise HTTPException(
            status_code=403,
            detail=f"taiji ingest 的 API Key 必须限定 1 个 cloud_account_id（当前 {len(restricted)} 个）",
        )
    ca_id = int(restricted[0])

    # 3. 验证 CloudAccount 是 taiji provider
    ca = await db.get(CloudAccount, ca_id)
    if not ca:
        raise HTTPException(status_code=403, detail=f"cloud_account_id={ca_id} 不存在")
    if ca.provider != "taiji":
        raise HTTPException(
            status_code=403,
            detail=f"cloud_account_id={ca_id} 的 provider={ca.provider!r}，不是 taiji",
        )

    # 4. 定位该 CloudAccount 下的唯一活跃 DataSource
    ds_rows = (await db.execute(
        select(DataSource).where(
            DataSource.cloud_account_id == ca_id,
        )
    )).scalars().all()
    if len(ds_rows) == 0:
        raise HTTPException(
            status_code=409,
            detail=f"cloud_account_id={ca_id} 下没有 DataSource（先跑 seed_taiji_data_source.py）",
        )
    if len(ds_rows) > 1:
        raise HTTPException(
            status_code=409,
            detail=f"cloud_account_id={ca_id} 下有 {len(ds_rows)} 个 DataSource（应仅 1 个）",
        )
    ds = ds_rows[0]
    quota_per_usd = int((ds.config or {}).get("quota_per_usd") or 500000)

    # 5. 调用同步引擎层（独立 DB 连接，与 FastAPI 异步 session 不冲突）
    from app.services.sync_service import (
        upsert_taiji_raw_logs,
        reaggregate_from_taiji_raw,
    )

    logs_dicts = [lg.model_dump() for lg in body.logs]

    store_stat = upsert_taiji_raw_logs(logs_dicts, data_source_id=ds.id)

    dates = sorted({
        dt.datetime.fromtimestamp(int(lg["created_at"]), tz=dt.timezone.utc).date().isoformat()
        for lg in logs_dicts
        if lg.get("created_at")
    })

    reagg = reaggregate_from_taiji_raw(ds.id, dates, quota_per_usd=quota_per_usd)

    return TaijiIngestResponse(
        received=len(body.logs),
        stored_new=store_stat["stored_new"],
        deduped=store_stat["deduped"],
        dates_reaggregated=dates,
        billing_rows_upserted=reagg["billing_rows"],
        token_usage_rows_upserted=reagg["token_usage_rows"],
        projects_created=reagg["projects_created"],
    )
