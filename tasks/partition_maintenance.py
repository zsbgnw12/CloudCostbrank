"""分区维护任务 — billing_summary 月分区幂等创建 + default 分区数据兜底修复。

按 v3-final §6 规格：
  - ensure_billing_summary_partition: 检查未来 N 个月分区是否存在，缺则建。
    若 default 分区有行 → 触发 fix_default_partition。
  - fix_default_partition: 把 default 分区的数据按 date 移到对应月分区。
    用 detach default → CREATE 新月分区 → INSERT/DELETE → attach default 流程。

降级说明（v3 §6）：默认告警通道走 logger.warning + sync_logs 一行；
alert_rules 接入留 Phase 2，避免 Phase 1 因告警链路引入新依赖。
"""

import datetime as dt
import logging

from sqlalchemy import text

from tasks.celery_app import celery_app
from app.services.sync_service import _get_sync_engine

logger = logging.getLogger(__name__)


def _next_month(d: dt.date) -> dt.date:
    if d.month == 12:
        return dt.date(d.year + 1, 1, 1)
    return dt.date(d.year, d.month + 1, 1)


def _month_floor(d: dt.date) -> dt.date:
    return dt.date(d.year, d.month, 1)


def _partition_name(month_start: dt.date) -> str:
    return f"billing_summary_{month_start.year:04d}{month_start.month:02d}"


def _log_to_sync_logs(conn, message: str, status: str = "success"):
    """Phase 1 降级：写一行 sync_logs 标记分区维护事件，data_source_id=0 标记为系统操作。

    若 0 不是合法 data_source_id，PG 会因 FK 报错；这里用 try 兜底，
    失败只 logger.warning 不阻塞主流程。
    """
    try:
        # data_source_id=0 一般不存在 → 用 NULL 风格：
        # 但 sync_logs.data_source_id 是 NOT NULL FK，无法插入。降级为 logger 即可。
        # 若未来想真的写库，需要先建一个"system"伪 data_source。
        logger.info("[partition_maintenance/%s] %s", status, message)
    except Exception as e:  # pragma: no cover
        logger.warning("partition_maintenance log failed: %s", e)


@celery_app.task(name="tasks.partition_maintenance.ensure_billing_summary_partition")
def ensure_billing_summary_partition(months_ahead: int = 3) -> dict:
    """幂等：保证未来 N 个月的分区存在；default 分区有数据则触发修复。

    Returns:
        {"created": [...], "default_rows": N, "fix_triggered": bool}
    """
    engine = _get_sync_engine()
    today = dt.date.today()
    cur = _month_floor(today)

    created = []
    with engine.begin() as conn:
        # 创建当月 + 未来 months_ahead 月的分区
        for _ in range(months_ahead + 1):
            nxt = _next_month(cur)
            part_name = _partition_name(cur)
            # 检查是否已存在；不存在则建
            exists = conn.execute(text(
                "SELECT to_regclass(:n) IS NOT NULL"
            ), {"n": f"public.{part_name}"}).scalar()
            if not exists:
                conn.execute(text(
                    f"CREATE TABLE IF NOT EXISTS {part_name} "
                    f"PARTITION OF billing_summary "
                    f"FOR VALUES FROM ('{cur.isoformat()}') TO ('{nxt.isoformat()}')"
                ))
                created.append(part_name)
                logger.info("Created partition %s [%s, %s)", part_name, cur, nxt)
            cur = nxt

        # 检查 default 分区行数
        default_rows = conn.execute(text(
            "SELECT COUNT(*) FROM billing_summary_default"
        )).scalar() or 0

    fix_triggered = False
    if default_rows > 0:
        logger.warning(
            "billing_summary_default has %d rows — triggering fix_default_partition",
            default_rows,
        )
        fix_default_partition.delay()
        fix_triggered = True

    return {
        "created": created,
        "default_rows": int(default_rows),
        "fix_triggered": fix_triggered,
    }


@celery_app.task(name="tasks.partition_maintenance.fix_default_partition")
def fix_default_partition() -> dict:
    """把 default 分区中所有数据按月迁回正确的月分区。

    流程：
      1. SELECT DISTINCT date_trunc('month', date) FROM billing_summary_default
      2. 对每个月：
         a) detach default 分区（让"目标月范围"对父表来说不再被 default 覆盖）
         b) 建月分区（IF NOT EXISTS）
         c) INSERT 新月分区 SELECT FROM default + DELETE FROM default
         d) re-attach default
      3. 写日志 / 告警（Phase 1 降级：只 logger.warning + sync_logs）
    """
    engine = _get_sync_engine()
    moved = []

    with engine.begin() as conn:
        rows = conn.execute(text(
            "SELECT DISTINCT date_trunc('month', date)::date AS m "
            "FROM billing_summary_default ORDER BY m"
        )).fetchall()

        if not rows:
            logger.info("fix_default_partition: default partition empty, nothing to do")
            return {"moved": [], "month_count": 0}

        # detach default 一次（避免每次循环 detach/attach）
        conn.execute(text(
            "ALTER TABLE billing_summary DETACH PARTITION billing_summary_default"
        ))

        try:
            for (month_start,) in rows:
                nxt = _next_month(month_start)
                part_name = _partition_name(month_start)

                # 创建月分区（PARTITION OF 父表）
                conn.execute(text(
                    f"CREATE TABLE IF NOT EXISTS {part_name} "
                    f"PARTITION OF billing_summary "
                    f"FOR VALUES FROM ('{month_start.isoformat()}') "
                    f"TO ('{nxt.isoformat()}')"
                ))

                # 把 default 里属于此月的数据搬过去
                # 因为 default 已被 detach，是普通表，可以 INSERT/DELETE
                result = conn.execute(text(
                    f"WITH moved_rows AS ("
                    f"  DELETE FROM billing_summary_default "
                    f"  WHERE date >= :m AND date < :n RETURNING * "
                    f") INSERT INTO {part_name} "
                    f"SELECT * FROM moved_rows"
                ), {"m": month_start, "n": nxt})
                count = result.rowcount or 0
                moved.append({"month": month_start.isoformat(), "rows": count})
                logger.info(
                    "fix_default_partition: moved %d rows from default to %s",
                    count, part_name,
                )
        finally:
            # attach default 回去
            conn.execute(text(
                "ALTER TABLE billing_summary ATTACH PARTITION billing_summary_default DEFAULT"
            ))

        _log_to_sync_logs(
            conn,
            f"Default partition repaired: {moved}",
            status="success",
        )

    logger.warning(
        "fix_default_partition completed: %d months reorganized: %s",
        len(moved), moved,
    )
    return {"moved": moved, "month_count": len(moved)}
