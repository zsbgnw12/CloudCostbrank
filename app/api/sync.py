"""Data sync API routes."""

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_roles
from app.database import get_db
from app.models.sync_log import SyncLog
from app.schemas.billing import SyncRequest, SyncLogRead

logger = logging.getLogger(__name__)

# Trigger & log routes require cloud_ops or cloud_admin.
router = APIRouter(dependencies=[Depends(require_roles("cloud_ops"))])


@router.get("/last")
async def last_sync(db: AsyncSession = Depends(get_db)):
    """Return the most recent successful sync end_time.

    On DB errors (unreachable DB, missing table, etc.) returns 200 with last_sync=null
    so the UI header does not break; check server logs for the real error.
    """
    try:
        result = await db.execute(
            select(SyncLog.end_time)
            .where(SyncLog.status == "success", SyncLog.end_time.isnot(None))
            .order_by(SyncLog.end_time.desc())
            .limit(1)
        )
        row = result.first()
        if row is None:
            return {"last_sync": None}
        val = row[0]
        return {"last_sync": val.isoformat() if hasattr(val, "isoformat") else str(val)}
    except Exception as e:
        logger.warning("GET /api/sync/last failed: %s", e, exc_info=True)
        return {"last_sync": None}


@router.post("/all")
async def sync_all(body: SyncRequest):
    from tasks.sync_tasks import sync_all as sync_all_task

    result = sync_all_task.delay(body.start_month, body.end_month, body.provider)
    return {"task_id": result.id, "status": "dispatched"}


@router.post("/refresh-summary")
async def refresh_summary(
    start_date: str | None = None,
    end_date: str | None = None,
):
    """Rebuild billing_daily_summary for a date range (or full rebuild if omitted).

    Use after manual data imports or corrections that bypass the normal sync pipeline.
    """
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
async def sync_one(data_source_id: int, body: SyncRequest):
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
):
    stmt = select(SyncLog).order_by(SyncLog.id.desc()).limit(limit)
    if data_source_id:
        stmt = stmt.where(SyncLog.data_source_id == data_source_id)
    if status:
        stmt = stmt.where(SyncLog.status == status)
    result = await db.execute(stmt)
    return result.scalars().all()
