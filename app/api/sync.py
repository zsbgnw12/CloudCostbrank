"""Data sync API routes."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_principal, require_roles
from app.auth.principal import Principal
from app.auth.scope import (
    ensure_data_source_visible,
    extract_providers_from_roles,
    has_full_access,
    visible_data_source_ids,
)
from app.database import get_db
from app.models.sync_log import SyncLog
from app.schemas.billing import SyncRequest, SyncLogRead

logger = logging.getLogger(__name__)

# router 级"是否云管角色"由 main.py 的 _cloud("sync") 完成。
# 数据范围由各 endpoint 内部用 visible_data_source_ids / ensure_data_source_visible 限定。
router = APIRouter()


@router.get("/last")
async def last_sync(
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    """Return the most recent successful sync end_time(within scope)。"""
    try:
        stmt = (
            select(SyncLog.end_time)
            .where(SyncLog.status == "success", SyncLog.end_time.isnot(None))
            .order_by(SyncLog.end_time.desc())
            .limit(1)
        )
        if not has_full_access(principal):
            visible = await visible_data_source_ids(db, principal)
            if not visible:
                return {"last_sync": None}
            stmt = stmt.where(SyncLog.data_source_id.in_(visible))
        result = await db.execute(stmt)
        row = result.first()
        if row is None:
            return {"last_sync": None}
        val = row[0]
        return {"last_sync": val.isoformat() if hasattr(val, "isoformat") else str(val)}
    except Exception as e:
        logger.warning("GET /api/sync/last failed: %s", e, exc_info=True)
        return {"last_sync": None}


@router.post("/all")
async def sync_all(
    body: SyncRequest,
    principal: Principal = Depends(get_current_principal),
):
    """触发全量同步。
    - admin/ops:不限,body.provider 为空 → 同步全部 provider
    - cloud_<provider> 用户:自动限制为自己 provider 范围
        · body.provider 为空:为每个 visible provider 各 dispatch 一次
        · body.provider 指定但不在用户范围:403
    """
    from tasks.sync_tasks import sync_all as sync_all_task

    if has_full_access(principal):
        result = sync_all_task.delay(body.start_month, body.end_month, body.provider)
        return {"task_id": result.id, "status": "dispatched"}

    # 云角色:按 provider 范围自动限定
    my_providers = extract_providers_from_roles(principal.roles)
    if not my_providers:
        raise HTTPException(403, "No cloud provider role assigned")

    if body.provider:
        if body.provider not in my_providers:
            raise HTTPException(
                403,
                f"Provider '{body.provider}' out of your scope (you can sync: {','.join(my_providers)})",
            )
        result = sync_all_task.delay(body.start_month, body.end_month, body.provider)
        return {"task_id": result.id, "status": "dispatched"}

    # 没指定 provider:为每个 visible provider 各 dispatch 一次
    task_ids = []
    for p in my_providers:
        r = sync_all_task.delay(body.start_month, body.end_month, p)
        task_ids.append(r.id)
    return {"task_ids": task_ids, "status": "dispatched", "providers": my_providers}


@router.post("/refresh-summary",
             dependencies=[Depends(require_roles("cloud_admin", "cloud_ops"))])
async def refresh_summary(
    start_date: str | None = None,
    end_date: str | None = None,
):
    """Rebuild billing_daily_summary for a date range (or full rebuild if omitted)."""
    from app.services.sync_service import refresh_daily_summary, _get_sync_engine
    from sqlalchemy.orm import Session

    engine = _get_sync_engine()
    with Session(engine) as session:
        if not start_date or not end_date:
            row = session.execute(
                text("SELECT MIN(date)::text, MAX(date)::text FROM billing_summary")
            ).first()
            if not row or row[0] is None:
                return {"status": "skipped", "reason": "no billing data"}
            start_date, end_date = row[0], row[1]

    refresh_daily_summary(start_date, end_date)
    return {"status": "ok", "refreshed_range": f"{start_date} ~ {end_date}"}


@router.post("/{data_source_id}")
async def sync_one(
    data_source_id: int,
    body: SyncRequest,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    # 数据范围:用户必须能管该 data_source 所属 cloud_account
    await ensure_data_source_visible(db, principal, data_source_id)
    from tasks.sync_tasks import sync_data_source
    result = sync_data_source.delay(data_source_id, body.start_month, body.end_month)
    return {"task_id": result.id, "status": "dispatched"}


@router.get("/status/{task_id}")
async def sync_status(task_id: str):
    from tasks.celery_app import celery_app

    result = celery_app.AsyncResult(task_id)
    return {
        "task_id": task_id,
        "status": result.status,
        "result": result.result if result.ready() else None,
    }


@router.get("/logs", response_model=list[SyncLogRead])
async def sync_logs(
    data_source_id: int | None = None,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    stmt = select(SyncLog).order_by(SyncLog.id.desc()).limit(limit)
    # 数据范围
    if not has_full_access(principal):
        visible = await visible_data_source_ids(db, principal)
        if not visible:
            return []
        stmt = stmt.where(SyncLog.data_source_id.in_(visible))
    if data_source_id:
        stmt = stmt.where(SyncLog.data_source_id == data_source_id)
    if status:
        stmt = stmt.where(SyncLog.status == status)
    result = await db.execute(stmt)
    return result.scalars().all()
