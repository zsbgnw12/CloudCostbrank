"""Compare what the DB (what the frontend reads) has for ocid-20260212 in April."""
import os
from pathlib import Path

from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

url = os.environ["SYNC_DATABASE_URL"]
engine = create_engine(url)

PROJECT = "ocid-20260212"
START = "2026-04-01"
END = "2026-04-15"

with engine.connect() as c:
    print("=== data_sources ===")
    rows = c.execute(text("""
        SELECT ds.id, ds.name, ca.provider, ds.is_active, ds.last_sync_at, ds.sync_status, ds.config
        FROM data_sources ds JOIN cloud_accounts ca ON ca.id = ds.cloud_account_id
        ORDER BY ds.id
    """)).fetchall()
    for r in rows:
        cfg = r.config or {}
        print(f"  id={r.id} name={r.name} provider={r.provider} active={r.is_active} last_sync={r.last_sync_at} status={r.sync_status}")
        print(f"    config: project={cfg.get('project_id')} dataset={cfg.get('dataset')} table={cfg.get('table')}")

    print(f"\n=== billing_data for project={PROJECT} {START}..{END} ===")
    rows = c.execute(text("""
        SELECT data_source_id, date, SUM(cost) AS c, COUNT(*) AS n
        FROM billing_data
        WHERE project_id = :p AND date BETWEEN :s AND :e
        GROUP BY data_source_id, date
        ORDER BY data_source_id, date
    """), {"p": PROJECT, "s": START, "e": END}).fetchall()
    total = 0.0
    by_ds = {}
    for r in rows:
        print(f"  ds={r.data_source_id} date={r.date} cost={float(r.c):.4f} n={r.n}")
        total += float(r.c)
        by_ds.setdefault(r.data_source_id, 0.0)
        by_ds[r.data_source_id] += float(r.c)
    print(f"\n  TOTAL in DB: {total:.4f}")
    print(f"  By data_source: {by_ds}")

    print(f"\n=== daily totals (frontend view) ===")
    rows = c.execute(text("""
        SELECT date, SUM(cost) AS c
        FROM billing_data
        WHERE project_id = :p AND date BETWEEN :s AND :e
        GROUP BY date ORDER BY date
    """), {"p": PROJECT, "s": START, "e": END}).fetchall()
    for r in rows:
        print(f"  {r.date}  {float(r.c):.4f}")

    print(f"\n=== sync_logs columns ===")
    cols = c.execute(text("""
        SELECT column_name FROM information_schema.columns WHERE table_name='sync_logs' ORDER BY ordinal_position
    """)).fetchall()
    print("  ", [x.column_name for x in cols])
