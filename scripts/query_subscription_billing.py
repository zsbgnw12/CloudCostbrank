"""Query billing_data + sync_logs for an Azure subscription GUID (project_id). Usage (from cloudcost/):

  python scripts/query_subscription_billing.py 09e4b3a6-8159-4f14-b108-e4a18ace9212

Reads SYNC_DATABASE_URL from .env (SQLAlchemy URL is normalized for psycopg2).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def load_sync_url() -> str:
    env = Path(".env")
    if not env.is_file():
        raise SystemExit(".env not found (run from cloudcost/ directory)")
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("SYNC_DATABASE_URL="):
            u = line.split("=", 1)[1].strip().strip('"')
            for prefix in ("postgresql+psycopg2://", "postgresql+asyncpg://"):
                if u.startswith(prefix):
                    u = "postgresql://" + u[len(prefix) :]
                    break
            return u
    raise SystemExit("SYNC_DATABASE_URL not in .env")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("subscription_id", help="Azure subscription GUID (same as billing_data.project_id)")
    args = p.parse_args()
    sub = args.subscription_id.strip()

    import psycopg2
    from psycopg2.extras import RealDictCursor

    conn = psycopg2.connect(load_sync_url())
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute(
        """
        SELECT COUNT(*) AS n, COALESCE(SUM(cost), 0) AS total_cost,
               MIN(date) AS min_d, MAX(date) AS max_d
        FROM billing_data WHERE project_id = %s
        """,
        (sub,),
    )
    print("billing_data (project_id = subscription):")
    print(dict(cur.fetchone()))

    cur.execute(
        """
        SELECT data_source_id, COUNT(*) AS n, COALESCE(SUM(cost), 0) AS sum_cost
        FROM billing_data WHERE project_id = %s
        GROUP BY data_source_id ORDER BY n DESC
        """,
        (sub,),
    )
    print("\nby data_source_id:")
    for r in cur.fetchall():
        print(dict(r))

    cur.execute(
        """
        SELECT ds.id, ds.name, ca.name AS account_name, ca.provider, ds.config
        FROM data_sources ds
        JOIN cloud_accounts ca ON ca.id = ds.cloud_account_id
        WHERE ds.id IN (
            SELECT DISTINCT data_source_id FROM billing_data WHERE project_id = %s
        )
        """,
        (sub,),
    )
    print("\ndata_sources that contributed rows:")
    for r in cur.fetchall():
        print(dict(r))

    cur.execute(
        """
        SELECT id, status, records_fetched, records_upserted,
               LEFT(COALESCE(error_message,''), 200) AS err,
               query_start_date, query_end_date, end_time
        FROM sync_logs
        WHERE data_source_id IN (
            SELECT DISTINCT data_source_id FROM billing_data WHERE project_id = %s
        )
        ORDER BY id DESC LIMIT 8
        """,
        (sub,),
    )
    print("\nrecent sync_logs for those data sources:")
    for r in cur.fetchall():
        print(dict(r))

    cur.close()
    conn.close()


if __name__ == "__main__":
    os.chdir(Path(__file__).resolve().parent.parent)
    main()
