"""Celery application instance and Beat schedule."""

from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery_app = Celery(
    "cloudcost",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["tasks.sync_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=False,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
)


celery_app.conf.beat_schedule = {
    "daily-sync": {
        "task": "tasks.sync_tasks.sync_all_current_month",
        "schedule": crontab(hour=2, minute=0),
    },
    "daily-alert-check": {
        "task": "tasks.sync_tasks.check_alerts",
        "schedule": crontab(hour=3, minute=0),
    },
    "monthly-bill-generate": {
        "task": "tasks.sync_tasks.generate_monthly_bills_previous",
        "schedule": crontab(day_of_month=2, hour=5, minute=0),
    },
    "daily-taiji-raw-gc": {
        "task": "tasks.sync_tasks.gc_taiji_raw_logs",
        "schedule": crontab(hour=4, minute=0),
    },
}

# Auto-discover tasks
celery_app.autodiscover_tasks(["tasks"])
