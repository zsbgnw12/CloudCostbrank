"""Celery sync tasks."""

import calendar
import datetime as dt
import json
import logging

from tasks.celery_app import celery_app
from app.services.sync_service import (
    get_active_data_sources,
    create_sync_log,
    complete_sync_log,
    update_data_source_sync_status,
    upsert_billing_rows,
    refresh_daily_summary,
    auto_create_gcp_projects,
)

logger = logging.getLogger(__name__)


def _month_to_date_range(start_month: str, end_month: str | None = None):
    """Convert YYYY-MM to (start_date, end_date) strings."""
    end_month = end_month or start_month
    start_date = f"{start_month}-01"
    y, m = map(int, end_month.split("-"))
    last_day = calendar.monthrange(y, m)[1]
    end_date = f"{end_month}-{last_day}"
    return start_date, end_date


@celery_app.task(bind=True, max_retries=3, soft_time_limit=1800, time_limit=2400)
def sync_data_source(self, data_source_id: int, start_month: str, end_month: str | None = None):
    """Sync a single data source."""
    from app.services.crypto_service import decrypt_to_dict
    from app.collectors import get_collector

    start_date, end_date = _month_to_date_range(start_month, end_month)

    log_id = None
    try:
        log_id = create_sync_log(data_source_id, self.request.id, start_date, end_date)
        update_data_source_sync_status(data_source_id, "running")

        from sqlalchemy.orm import Session
        from app.models.data_source import DataSource
        from app.models.cloud_account import CloudAccount
        from app.services.sync_service import _get_sync_engine

        engine = _get_sync_engine()
        with Session(engine) as session:
            ds = session.get(DataSource, data_source_id)
            if not ds:
                raise ValueError(f"DataSource {data_source_id} not found")
            ca = session.get(CloudAccount, ds.cloud_account_id)
            if not ca:
                raise ValueError(f"CloudAccount {ds.cloud_account_id} not found")
            provider = ca.provider
            config = ds.config
            secret_data = decrypt_to_dict(ca.secret_data)

        collector = get_collector(provider)
        rows = collector.collect_billing(secret_data, config, start_date, end_date)

        for row in rows:
            row["data_source_id"] = data_source_id
            row["provider"] = provider
            if isinstance(row.get("tags"), (dict, list)):
                row["tags"] = json.dumps(row["tags"], ensure_ascii=False)
            elif not row.get("tags"):
                row["tags"] = "{}"
            if isinstance(row.get("additional_info"), (dict, list)):
                row["additional_info"] = json.dumps(row["additional_info"], ensure_ascii=False)
            elif not row.get("additional_info"):
                row["additional_info"] = "{}"

        upserted = upsert_billing_rows(rows)

        if provider == "gcp":
            try:
                created = auto_create_gcp_projects(rows)
                if created:
                    logger.info("Auto-created %d new GCP project(s) (unassigned bucket)", created)
            except Exception as e:
                logger.warning("Failed to auto-create GCP projects: %s", e)

        try:
            refresh_daily_summary(start_date, end_date)
        except Exception as e:
            logger.warning("Failed to refresh daily_summary: %s", e)

        complete_sync_log(log_id, records_fetched=len(rows), records_upserted=upserted)
        update_data_source_sync_status(data_source_id, "success")

        return {"data_source_id": data_source_id, "fetched": len(rows), "upserted": upserted}

    except Exception as exc:
        if log_id is not None:
            complete_sync_log(log_id, records_fetched=0, records_upserted=0, error=str(exc))
        update_data_source_sync_status(data_source_id, "failed")
        raise self.retry(exc=exc, countdown=60 * (self.request.retries + 1))


@celery_app.task
def sync_all(start_month: str, end_month: str | None = None, provider: str | None = None):
    """Dispatch sync tasks for all active data sources."""
    sources = get_active_data_sources()
    if provider:
        sources = [s for s in sources if s["provider"] == provider]
    task_ids = []
    for src in sources:
        result = sync_data_source.delay(src["data_source_id"], start_month, end_month)
        task_ids.append(result.id)
    return {"dispatched": len(task_ids), "task_ids": task_ids}


@celery_app.task
def sync_all_current_month():
    """Beat wrapper: compute current month at runtime, then dispatch."""
    month = dt.date.today().strftime("%Y-%m")
    return sync_all(month)


@celery_app.task
def check_alerts():
    """Run alert checks."""
    from app.services.alert_service import check_all_alerts
    check_all_alerts()


@celery_app.task
def generate_monthly_bills(month: str):
    """Generate monthly bills (sync wrapper)."""
    from app.services.bill_service import generate_bills
    import asyncio

    async def _run():
        from app.database import async_session_factory
        async with async_session_factory() as db:
            count = await generate_bills(db, month)
            await db.commit()
            return count

    count = asyncio.run(_run())
    return {"month": month, "generated": count}


@celery_app.task
def generate_monthly_bills_previous():
    """Beat wrapper: compute previous month at runtime, then dispatch."""
    today = dt.date.today()
    first = today.replace(day=1)
    prev = first - dt.timedelta(days=1)
    month = prev.strftime("%Y-%m")
    return generate_monthly_bills(month)
