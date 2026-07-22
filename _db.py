"""Shared DB helper with retry on transient Azure PG flaps.

安全:连接串来自环境变量 SYNC_DATABASE_URL / DATABASE_URL(或 app 的 .env,
经 app.config.settings)。**不再硬编码任何数据库凭据**。运行本仓 ops 脚本前,
请在环境中设置 SYNC_DATABASE_URL(不要写进代码或提交进 Git)。
"""
import os
import time

import psycopg2


def _dsn() -> str:
    url = os.environ.get("SYNC_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        try:
            import sys
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from app.config import settings
            url = settings.SYNC_DATABASE_URL
        except Exception:
            url = ""
    if not url or "user:password@localhost" in url:
        raise RuntimeError(
            "未配置数据库连接串。请在环境变量 SYNC_DATABASE_URL 中设置真实连接串"
            "(禁止硬编码到代码或提交进仓库)。"
        )
    # psycopg2 不认识 +psycopg2 / +asyncpg 方言后缀
    return (
        url.replace("postgresql+psycopg2://", "postgresql://")
        .replace("postgresql+asyncpg://", "postgresql://")
    )


def connect(readonly=False, retries=5):
    dsn = _dsn()
    last = None
    for i in range(retries):
        try:
            c = psycopg2.connect(
                dsn, sslmode="require", connect_timeout=60,
                keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=5,
            )
            if readonly:
                c.set_session(readonly=True)
            return c
        except Exception as e:
            last = e
            print(f"  (connect retry {i+1}/{retries}: {type(e).__name__})")
            time.sleep(3 * (i + 1))
    raise last


def run_q(q, params=None, readonly=True):
    """Run one query with automatic reconnect, returns rows."""
    for attempt in range(5):
        try:
            c = connect(readonly=readonly)
            cur = c.cursor()
            cur.execute(q, params)
            if cur.description:
                rows = cur.fetchall()
            else:
                rows = None
            if not readonly:
                c.commit()
            c.close()
            return rows, cur.rowcount
        except psycopg2.OperationalError as e:
            print(f"  (query retry {attempt+1}: {type(e).__name__})")
            time.sleep(3 * (attempt + 1))
    raise RuntimeError("Exhausted retries")
