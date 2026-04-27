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
    auto_create_taiji_projects,
    upsert_token_usage_rows,
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

        # 把 dict/list 字段序列化成 JSON 字符串：COPY → JSONB 列要求双引号 JSON。
        # Python str(dict) 用单引号，PG 解析会失败，所以必须 json.dumps。
        for row in rows:
            row["data_source_id"] = data_source_id
            row["provider"] = provider
            # tags / additional_info：保留老行为 —— 空时写 "{}"（向后兼容已有数据形态）
            for f in ("tags", "additional_info"):
                v = row.get(f)
                if isinstance(v, (dict, list)):
                    row[f] = json.dumps(v, ensure_ascii=False)
                elif not v:
                    row[f] = "{}"
            # system_labels / credits_breakdown：新字段，允许 NULL（区分"没数据"和"空")
            for f in ("system_labels", "credits_breakdown"):
                v = row.get(f)
                if isinstance(v, (dict, list)):
                    row[f] = json.dumps(v, ensure_ascii=False) if v else None
                elif v in ("", {}):
                    row[f] = None

        upserted = upsert_billing_rows(rows)

        if provider == "gcp":
            try:
                created = auto_create_gcp_projects(rows)
                if created:
                    logger.info("Auto-created %d new GCP project(s) (unassigned bucket)", created)
            except Exception as e:
                logger.warning("Failed to auto-create GCP projects: %s", e)

        if provider == "taiji":
            try:
                created = auto_create_taiji_projects(rows, data_source_id=data_source_id)
                if created:
                    logger.info("Auto-created %d new Taiji token project(s)", created)
            except Exception as e:
                logger.warning("Failed to auto-create taiji projects: %s", e)
            try:
                tu_count = upsert_token_usage_rows(rows, provider=provider, data_source_id=data_source_id)
                if tu_count:
                    logger.info("Upserted %d token_usage rows for taiji ds=%d", tu_count, data_source_id)
            except Exception as e:
                logger.warning("Failed to upsert token_usage for taiji: %s", e)

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
def gc_taiji_raw_logs():
    """每日清理 30 天前的 taiji 原始请求日志；天级聚合（billing_data / token_usage）不动。"""
    from app.services.sync_service import gc_taiji_raw_older_than
    deleted = gc_taiji_raw_older_than(days=30)
    return {"deleted": deleted}


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
