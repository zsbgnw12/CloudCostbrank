"""Sync orchestration service (called by Celery tasks)."""

import datetime as dt
import io
import logging

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.config import settings
from app.models.data_source import DataSource
from app.models.cloud_account import CloudAccount
from app.models.project import Project
from app.models.supply_source import SupplySource
from app.models.sync_log import SyncLog
from app.services.crypto_service import decrypt_to_dict
from app.services.default_supply_sources import ensure_other_gcp_supply_source_id_sync

logger = logging.getLogger(__name__)

# Synchronous engine for Celery workers
_sync_engine = None


def _get_sync_engine():
    global _sync_engine
    if _sync_engine is None:
        _sync_engine = create_engine(
            settings.SYNC_DATABASE_URL,
            pool_size=5,
            pool_pre_ping=True,
            pool_recycle=1800,
        )
    return _sync_engine


def get_active_data_sources() -> list[dict]:
    """Return all active data sources with their cloud account info."""
    engine = _get_sync_engine()
    with Session(engine) as session:
        rows = (
            session.query(DataSource, CloudAccount)
            .join(CloudAccount, DataSource.cloud_account_id == CloudAccount.id)
            .filter(DataSource.is_active.is_(True))
            .all()
        )
        return [
            {
                "data_source_id": ds.id,
                "provider": ca.provider,
                "config": ds.config,
                "secret_data": decrypt_to_dict(ca.secret_data),
            }
            for ds, ca in rows
        ]


def create_sync_log(data_source_id: int, celery_task_id: str, start_date: str, end_date: str) -> int:
    engine = _get_sync_engine()
    with Session(engine) as session:
        log = SyncLog(
            data_source_id=data_source_id,
            celery_task_id=celery_task_id,
            start_time=dt.datetime.utcnow(),
            status="running",
            query_start_date=dt.date.fromisoformat(start_date),
            query_end_date=dt.date.fromisoformat(end_date),
        )
        session.add(log)
        session.commit()
        return log.id


def complete_sync_log(log_id: int, *, records_fetched: int, records_upserted: int, error: str | None = None):
    engine = _get_sync_engine()
    with Session(engine) as session:
        log = session.get(SyncLog, log_id)
        if not log:
            return
        log.end_time = dt.datetime.utcnow()
        log.records_fetched = records_fetched
        log.records_upserted = records_upserted
        log.status = "failed" if error else "success"
        log.error_message = error
        session.commit()


def update_data_source_sync_status(data_source_id: int, status: str):
    engine = _get_sync_engine()
    with Session(engine) as session:
        ds = session.get(DataSource, data_source_id)
        if ds:
            ds.sync_status = status
            if status == "success":
                ds.last_sync_at = dt.datetime.utcnow()
            session.commit()


def _escape_copy_value(val) -> str:
    """Escape a value for PostgreSQL COPY text format."""
    if val is None:
        return "\\N"
    s = str(val)
    s = s.replace("\\", "\\\\")
    s = s.replace("\t", "\\t")
    s = s.replace("\n", "\\n")
    s = s.replace("\r", "\\r")
    return s


_BILLING_COLUMNS = [
    "date", "provider", "data_source_id", "project_id", "project_name",
    "product", "usage_type", "region", "cost", "usage_quantity",
    "usage_unit", "currency", "tags", "additional_info",
]


def upsert_billing_rows(rows: list[dict]):
    """Bulk upsert using COPY + temp table merge (50x faster than executemany)."""
    if not rows:
        return 0

    engine = _get_sync_engine()

    buf = io.StringIO()
    for row in rows:
        line = "\t".join(_escape_copy_value(row.get(c)) for c in _BILLING_COLUMNS)
        buf.write(line + "\n")
    buf.seek(0)

    cols_str = ", ".join(_BILLING_COLUMNS)

    with engine.begin() as conn:
        raw = conn.connection.dbapi_connection
        cur = raw.cursor()
        try:
            cur.execute("""
                CREATE TEMP TABLE _billing_staging (
                    date DATE, provider VARCHAR(10), data_source_id INTEGER,
                    project_id VARCHAR(200), project_name VARCHAR(200),
                    product VARCHAR(200), usage_type VARCHAR(300), region VARCHAR(50),
                    cost DECIMAL(20,6), usage_quantity DECIMAL(20,6),
                    usage_unit VARCHAR(50), currency VARCHAR(10),
                    tags JSONB, additional_info JSONB
                ) ON COMMIT DROP
            """)

            cur.copy_expert(
                f"COPY _billing_staging ({cols_str}) FROM STDIN WITH (FORMAT text)",
                buf,
            )
            logger.info("COPY %d rows into staging table", len(rows))

            cur.execute(f"""
                INSERT INTO billing_data ({cols_str})
                SELECT {cols_str} FROM (
                    SELECT DISTINCT ON (date, data_source_id, project_id, product, usage_type, region)
                        {cols_str}
                    FROM _billing_staging
                    ORDER BY date, data_source_id, project_id, product, usage_type, region, cost DESC
                ) AS deduped
                ON CONFLICT (date, data_source_id, project_id, product, usage_type, region)
                DO UPDATE SET
                    cost = EXCLUDED.cost,
                    usage_quantity = EXCLUDED.usage_quantity,
                    project_name = EXCLUDED.project_name,
                    currency = EXCLUDED.currency,
                    tags = EXCLUDED.tags,
                    additional_info = EXCLUDED.additional_info
            """)

            logger.info("Merged %d rows into billing_data", len(rows))
            return len(rows)
        finally:
            cur.close()


def refresh_daily_summary(start_date: str, end_date: str):
    """Refresh pre-aggregated daily summary for the given date range."""
    engine = _get_sync_engine()
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM billing_daily_summary WHERE date >= :sd AND date <= :ed"),
            {"sd": start_date, "ed": end_date},
        )
        conn.execute(text("""
            INSERT INTO billing_daily_summary
                (date, provider, data_source_id, project_id, product,
                 total_cost, total_usage, record_count)
            SELECT
                date, provider, data_source_id, project_id, product,
                SUM(cost), SUM(usage_quantity), COUNT(*)
            FROM billing_data
            WHERE date >= :sd AND date <= :ed
            GROUP BY date, provider, data_source_id, project_id, product
        """), {"sd": start_date, "ed": end_date})
        logger.info("Refreshed daily_summary for %s to %s", start_date, end_date)


def auto_create_gcp_projects(rows: list[dict]) -> int:
    """Auto-create Project records for GCP project_ids discovered in billing data.

    New projects are placed in the '其他货源' group so operators can later
    move them to the correct group.  Returns the number of newly created projects.
    """
    if not rows:
        return 0

    # Collect unique project_id → project_name from billing rows
    discovered: dict[str, str] = {}
    for row in rows:
        pid = row.get("project_id")
        if not pid:
            continue
        pid = str(pid).strip()
        if pid not in discovered:
            discovered[pid] = row.get("project_name") or pid

    if not discovered:
        return 0

    engine = _get_sync_engine()
    with Session(engine) as session:
        try:
            ss_id, bucket_supplier_name = ensure_other_gcp_supply_source_id_sync(session)
        except Exception as e:
            logger.warning("auto_create_gcp_projects: ensure default GCP 货源失败, skip: %s", e)
            return 0

        existing = set(
            r[0]
            for r in session.query(Project.external_project_id)
            .join(SupplySource, Project.supply_source_id == SupplySource.id)
            .filter(
                SupplySource.provider == "gcp",
                Project.external_project_id.in_(list(discovered.keys())),
            )
            .all()
        )

        new_projects = []
        for pid, pname in discovered.items():
            if pid in existing:
                continue
            project = Project(
                name=pname,
                external_project_id=pid,
                supply_source_id=ss_id,
                status="standby",
            )
            new_projects.append(project)

        if new_projects:
            session.add_all(new_projects)
            session.commit()
            logger.info(
                "Auto-created %d GCP project(s) under supplier %r: %s",
                len(new_projects),
                bucket_supplier_name,
                [p.external_project_id for p in new_projects],
            )

        return len(new_projects)
