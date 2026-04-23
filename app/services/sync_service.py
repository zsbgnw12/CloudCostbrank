"""Sync orchestration service (called by Celery tasks)."""

import datetime as dt
import io
import logging

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.models.data_source import DataSource
from app.models.cloud_account import CloudAccount
from app.models.project import Project
from app.models.supply_source import SupplySource
from app.models.sync_log import SyncLog
from app.models.taiji_log_raw import TaijiLogRaw
from app.services.crypto_service import decrypt_to_dict
from app.services.default_supply_sources import (
    ensure_other_gcp_supply_source_id_sync,
    ensure_other_taiji_supply_source_id_sync,
)

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

            # Aggregate by unique key before insert: SUM cost / usage_quantity so
            # multiple line-items sharing the same dedup key (common in GCP BQ and
            # Azure Cost Details CSV exports) are summed, not overwritten.
            cur.execute(f"""
                INSERT INTO billing_data ({cols_str})
                SELECT
                    date, provider, data_source_id, project_id,
                    MAX(project_name) AS project_name,
                    product, usage_type, region,
                    SUM(cost) AS cost,
                    SUM(usage_quantity) AS usage_quantity,
                    MAX(usage_unit) AS usage_unit,
                    MAX(currency) AS currency,
                    (ARRAY_AGG(tags ORDER BY cost DESC))[1] AS tags,
                    (ARRAY_AGG(additional_info ORDER BY cost DESC))[1] AS additional_info
                FROM _billing_staging
                GROUP BY date, provider, data_source_id, project_id, product, usage_type, region
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


def _resolve_supply_source_for_taiji(session, data_source_id: int) -> tuple[int, str]:
    """
    为新 taiji token 选定挂靠的 SupplySource。优先级：
      1. 同一 data_source_id 下已有的 Project 的归属 —— 复用（保证后续 token 归属稳定）
      2. 非"未分配资源组"的唯一用户级 taiji SupplySource —— 归档到用户建的业务组
      3. 兜底：回落到"未分配资源组 / taiji"（歧义或用户还没建业务组）
    返回 (supply_source_id, 归属描述)
    """
    from app.services.default_supply_sources import (
        RESERVED_UNASSIGNED_SUPPLIER_NAME,
        ensure_other_taiji_supply_source_id_sync as _ensure_default,
    )
    from app.models.supplier import Supplier as _Supplier

    # 1) 同 DS 已有 Project 的归属
    existing_ss = session.execute(
        select(Project.supply_source_id)
        .where(Project.data_source_id == data_source_id)
        .limit(1)
    ).scalar_one_or_none()
    if existing_ss:
        return existing_ss, f"复用 DS#{data_source_id} 既有 Project 归属 SS#{existing_ss}"

    # 2) 用户级 taiji 货源（非保留供应商下）
    reserved_sup_id = session.execute(
        select(_Supplier.id).where(_Supplier.name == RESERVED_UNASSIGNED_SUPPLIER_NAME)
    ).scalar_one_or_none()
    user_ss = session.execute(
        select(SupplySource).where(
            SupplySource.provider == "taiji",
            SupplySource.supplier_id != (reserved_sup_id or -1),
        )
    ).scalars().all()
    if len(user_ss) == 1:
        return user_ss[0].id, f"唯一用户级 taiji SS#{user_ss[0].id}"

    # 3) 兜底
    ss_id, sup_name = _ensure_default(session)
    if len(user_ss) > 1:
        return ss_id, f"用户级 taiji SS 有 {len(user_ss)} 个（歧义），兜底到 {sup_name}"
    return ss_id, f"无用户级 taiji SS，兜底到 {sup_name}"


def auto_create_taiji_projects(rows: list[dict], data_source_id: int) -> int:
    """
    自动发现 Taiji token：每次同步把新出现的 token_id（= project_id）建成 Project。

    归属决策见 `_resolve_supply_source_for_taiji`：优先用户建的 taiji 货源，
    歧义时退回"未分配资源组"。
    """
    if not rows:
        return 0

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
            ss_id, resolution = _resolve_supply_source_for_taiji(session, data_source_id)
        except Exception as e:
            logger.warning("auto_create_taiji_projects: resolve SS 失败: %s", e)
            return 0

        # 查重：已存在的 token 不再新建
        existing = set(
            r[0]
            for r in session.query(Project.external_project_id)
            .join(SupplySource, Project.supply_source_id == SupplySource.id)
            .filter(
                SupplySource.provider == "taiji",
                Project.external_project_id.in_(list(discovered.keys())),
            )
            .all()
        )

        new_projects = []
        for pid, pname in discovered.items():
            if pid in existing:
                continue
            new_projects.append(Project(
                name=pname,
                external_project_id=pid,
                supply_source_id=ss_id,
                data_source_id=data_source_id,  # 绑定 DS 便于后续复用归属
                status="standby",
            ))

        if new_projects:
            session.add_all(new_projects)
            session.commit()
            logger.info(
                "Auto-created %d taiji token project(s) under SS#%d (%s): %s",
                len(new_projects), ss_id, resolution,
                [p.external_project_id for p in new_projects],
            )

        return len(new_projects)


# ────────────────────── TokenUsage 聚合写入 ──────────────────────

# 使用列名形式的 ON CONFLICT，避免依赖约束名（线上 alembic 自动生成的名字可能与 ORM 的
# uix_token_usage_dedup 不同步；列名形式在列集合一致即可命中索引）。
_TOKEN_USAGE_UPSERT = text("""
    INSERT INTO token_usage (
        date, provider, data_source_id, model_id, model_name, region,
        request_count, input_tokens, output_tokens,
        cache_read_tokens, cache_write_tokens, total_tokens,
        input_cost, output_cost, total_cost, currency,
        additional_info
    ) VALUES (
        :date, :provider, :data_source_id, :model_id, :model_name, :region,
        :request_count, :input_tokens, :output_tokens,
        :cache_read_tokens, :cache_write_tokens, :total_tokens,
        :input_cost, :output_cost, :total_cost, :currency,
        '{}'::jsonb
    )
    ON CONFLICT (date, provider, data_source_id, model_id, region)
    DO UPDATE SET
        model_name = EXCLUDED.model_name,
        request_count = EXCLUDED.request_count,
        input_tokens = EXCLUDED.input_tokens,
        output_tokens = EXCLUDED.output_tokens,
        cache_read_tokens = EXCLUDED.cache_read_tokens,
        cache_write_tokens = EXCLUDED.cache_write_tokens,
        total_tokens = EXCLUDED.total_tokens,
        input_cost = EXCLUDED.input_cost,
        output_cost = EXCLUDED.output_cost,
        total_cost = EXCLUDED.total_cost,
        currency = EXCLUDED.currency
""")


def upsert_token_usage_rows(rows: list[dict], *, provider: str, data_source_id: int) -> int:
    """
    从 billing rows 的 `_token_usage` 子字典里按 (date, model_id, region) 再聚合一次，
    upsert 到 token_usage 表（该表的 unique key 是 (date, provider, data_source_id, model_id, region)）。

    region 为 None 时映射到空串 '' —— PostgreSQL 的 UNIQUE 对 NULL 不算冲突，统一存 '' 可保证幂等 upsert。
    """
    if not rows:
        return 0

    # 聚合键：(date, model_id, region)
    bucket: dict[tuple, dict] = {}
    for row in rows:
        tu = row.get("_token_usage")
        if not tu:
            continue
        key = (tu["date"], tu["model_id"], tu.get("region") or "")
        acc = bucket.get(key)
        if acc is None:
            acc = {
                "date": tu["date"],
                "provider": provider,
                "data_source_id": data_source_id,
                "model_id": tu["model_id"],
                "model_name": tu.get("model_name") or tu["model_id"],
                "region": tu.get("region") or "",
                "request_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "total_tokens": 0,
                "input_cost": 0.0,
                "output_cost": 0.0,
                "total_cost": 0.0,
                "currency": tu.get("currency") or "USD",
            }
            bucket[key] = acc
        acc["request_count"] += int(tu.get("request_count") or 0)
        acc["input_tokens"] += int(tu.get("input_tokens") or 0)
        acc["output_tokens"] += int(tu.get("output_tokens") or 0)
        acc["cache_read_tokens"] += int(tu.get("cache_read_tokens") or 0)
        acc["cache_write_tokens"] += int(tu.get("cache_write_tokens") or 0)
        acc["total_tokens"] += int(tu.get("total_tokens") or 0)
        acc["input_cost"] += float(tu.get("input_cost") or 0)
        acc["output_cost"] += float(tu.get("output_cost") or 0)
        acc["total_cost"] += float(tu.get("total_cost") or 0)

    if not bucket:
        return 0

    engine = _get_sync_engine()
    with engine.begin() as conn:
        for acc in bucket.values():
            conn.execute(_TOKEN_USAGE_UPSERT, acc)

    logger.info("Upserted %d token_usage rows (provider=%s, ds=%d)",
                len(bucket), provider, data_source_id)
    return len(bucket)


# ────────────────────── Taiji Push ingest ──────────────────────

_TAIJI_RAW_UPSERT = text("""
    INSERT INTO taiji_log_raw (
        data_source_id, id, date, created_at, type,
        user_id, username, token_id, token_name,
        channel_id, channel_name, model_name,
        quota, prompt_tokens, completion_tokens,
        use_time, is_stream, other
    ) VALUES (
        :data_source_id, :id, :date, :created_at, :type,
        :user_id, :username, :token_id, :token_name,
        :channel_id, :channel_name, :model_name,
        :quota, :prompt_tokens, :completion_tokens,
        :use_time, :is_stream, CAST(:other AS JSONB)
    )
    ON CONFLICT (data_source_id, id) DO NOTHING
""")


def upsert_taiji_raw_logs(logs: list[dict], *, data_source_id: int) -> dict:
    """
    把 Push 过来的 taiji 原始请求日志按 (data_source_id, id) 主键幂等入库。
    重复推送同一 id 走 ON CONFLICT DO NOTHING。

    Returns: {"received": N, "stored_new": M, "deduped": N-M}
    """
    import json as _json

    if not logs:
        return {"received": 0, "stored_new": 0, "deduped": 0}

    engine = _get_sync_engine()
    stored_new = 0

    rows = []
    for lg in logs:
        created_at = int(lg.get("created_at") or 0)
        if not created_at:
            continue
        date = dt.datetime.fromtimestamp(created_at, tz=dt.timezone.utc).date()

        other_val = lg.get("other")
        if isinstance(other_val, str):
            try:
                other_val = _json.loads(other_val) if other_val else None
            except (ValueError, TypeError):
                other_val = {"_raw": lg.get("other")}
        # 入库前统一成 JSON 字符串（绑 JSONB 列）
        other_json = _json.dumps(other_val, ensure_ascii=False) if other_val is not None else None

        rows.append({
            "data_source_id": data_source_id,
            "id": int(lg["id"]),
            "date": date,
            "created_at": created_at,
            "type": _nullable_int(lg.get("type")),
            "user_id": _nullable_int(lg.get("user_id")),
            "username": _clip_str(lg.get("username"), 200),
            "token_id": int(lg.get("token_id") or 0),
            "token_name": _clip_str(lg.get("token_name"), 200),
            "channel_id": _nullable_int(lg.get("channel_id")),
            "channel_name": _clip_str(lg.get("channel_name"), 200),
            "model_name": _clip_str(lg.get("model_name") or "unknown", 200) or "unknown",
            "quota": int(lg.get("quota") or 0),
            "prompt_tokens": int(lg.get("prompt_tokens") or 0),
            "completion_tokens": int(lg.get("completion_tokens") or 0),
            "use_time": _nullable_int(lg.get("use_time")),
            "is_stream": _nullable_int(lg.get("is_stream")),
            "other": other_json,
        })

    if not rows:
        return {"received": len(logs), "stored_new": 0, "deduped": len(logs)}

    with engine.begin() as conn:
        # ON CONFLICT DO NOTHING 不会报错重复；SQLAlchemy 对 text() 执行 N 次
        # 小批量单行执行最直观，2000 条以内延迟可接受（<1s）
        for r in rows:
            result = conn.execute(_TAIJI_RAW_UPSERT, r)
            stored_new += result.rowcount if result.rowcount and result.rowcount > 0 else 0

    return {
        "received": len(logs),
        "stored_new": stored_new,
        "deduped": len(logs) - stored_new,
    }


def reaggregate_from_taiji_raw(
    data_source_id: int,
    dates: list[str],  # YYYY-MM-DD
    *,
    quota_per_usd: int,
) -> dict:
    """
    从 taiji_log_raw 表按涉及日期重算 billing_data + token_usage，**先删后插**
    保证"taiji 原始日志 = billing/token 聚合"单一真相。

    返回 {"billing_rows": N, "token_usage_rows": M, "projects_created": K}
    """
    from app.collectors.taiji_collector import _aggregate_logs
    import json as _json

    if not dates:
        return {"billing_rows": 0, "token_usage_rows": 0, "projects_created": 0}

    date_objs = [dt.date.fromisoformat(d) for d in dates]
    engine = _get_sync_engine()

    # 1. 从原始表读 — 转成 taiji_collector._aggregate_logs 认的格式
    raw_dicts: list[dict] = []
    with Session(engine) as session:
        rows = session.execute(
            select(TaijiLogRaw).where(
                TaijiLogRaw.data_source_id == data_source_id,
                TaijiLogRaw.date.in_(date_objs),
            )
        ).scalars().all()
        for r in rows:
            raw_dicts.append({
                "id": r.id,
                "user_id": r.user_id,
                "created_at": r.created_at,
                "type": r.type,
                "username": r.username,
                "token_id": r.token_id,
                "token_name": r.token_name,
                "channel_id": r.channel_id,
                "channel_name": r.channel_name,
                "model_name": r.model_name,
                "quota": r.quota,
                "prompt_tokens": r.prompt_tokens,
                "completion_tokens": r.completion_tokens,
                "use_time": r.use_time,
                "is_stream": r.is_stream,
                "other": r.other,
            })

    # 2. 聚合
    agg_rows = _aggregate_logs(raw_dicts, quota_per_usd=quota_per_usd)

    # sync_tasks 里的字段规整逻辑在这儿复刻一份（provider、data_source_id、JSONB 字符串化）
    for row in agg_rows:
        row["data_source_id"] = data_source_id
        row["provider"] = "taiji"
        if isinstance(row.get("tags"), (dict, list)):
            row["tags"] = _json.dumps(row["tags"], ensure_ascii=False)
        elif not row.get("tags"):
            row["tags"] = "{}"
        if isinstance(row.get("additional_info"), (dict, list)):
            row["additional_info"] = _json.dumps(row["additional_info"], ensure_ascii=False)
        elif not row.get("additional_info"):
            row["additional_info"] = "{}"

    # 3. 先删后插 — 保证"原始即真相"
    with engine.begin() as conn:
        conn.execute(text("""
            DELETE FROM billing_data
            WHERE provider='taiji' AND data_source_id=:ds AND date = ANY(:dates)
        """), {"ds": data_source_id, "dates": date_objs})
        conn.execute(text("""
            DELETE FROM token_usage
            WHERE provider='taiji' AND data_source_id=:ds AND date = ANY(:dates)
        """), {"ds": data_source_id, "dates": date_objs})

    billing_count = upsert_billing_rows(agg_rows) if agg_rows else 0
    token_count = (
        upsert_token_usage_rows(agg_rows, provider="taiji", data_source_id=data_source_id)
        if agg_rows else 0
    )
    projects_created = auto_create_taiji_projects(agg_rows, data_source_id=data_source_id) if agg_rows else 0

    # 4. Dashboard 预聚合表也要刷
    for d in dates:
        try:
            refresh_daily_summary(d, d)
        except Exception as e:
            logger.warning("refresh_daily_summary(%s) failed: %s", d, e)

    return {
        "billing_rows": billing_count,
        "token_usage_rows": token_count,
        "projects_created": projects_created,
    }


def gc_taiji_raw_older_than(days: int = 30) -> int:
    """清理 N 天前的 taiji 原始日志；天级聚合不动。返回删除行数。"""
    engine = _get_sync_engine()
    cutoff = dt.date.today() - dt.timedelta(days=days)
    with engine.begin() as conn:
        result = conn.execute(
            text("DELETE FROM taiji_log_raw WHERE date < :cutoff"),
            {"cutoff": cutoff},
        )
        deleted = result.rowcount or 0
    logger.info("gc_taiji_raw_older_than(%d): deleted %d rows older than %s",
                days, deleted, cutoff)
    return deleted


def _nullable_int(v) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def _clip_str(v, max_len: int) -> str | None:
    if v is None:
        return None
    s = str(v)
    return s[:max_len] if len(s) > max_len else s
