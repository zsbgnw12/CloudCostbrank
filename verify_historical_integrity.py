"""Audit post-backfill state — is anything still suspicious?"""
import os, time
from pathlib import Path
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


def run(sql, params=None):
    for attempt in range(1, 5):
        try:
            engine = create_engine(os.environ["SYNC_DATABASE_URL"])
            with engine.connect() as c:
                return c.execute(text(sql), params or {}).fetchall()
        except Exception as ex:
            if "server closed" in repr(ex) and attempt < 4:
                time.sleep(3 * attempt)
                continue
            raise


print("=== (1) Per-month per-source row counts in billing_data (Jan~Apr 2026) ===")
rows = run("""
    SELECT ca.provider, ds.id AS ds_id, ds.name,
           TO_CHAR(DATE_TRUNC('month', bd.date), 'YYYY-MM') AS mon,
           COUNT(*) AS rows, SUM(bd.cost) AS total_cost
    FROM billing_data bd
    JOIN data_sources ds ON ds.id = bd.data_source_id
    JOIN cloud_accounts ca ON ca.id = ds.cloud_account_id
    WHERE bd.date BETWEEN '2026-01-01' AND '2026-04-30'
    GROUP BY ca.provider, ds.id, ds.name, mon
    ORDER BY ca.provider, ds.id, mon
""")
print(f"  {'prov':<7} {'ds':>4} {'name':<25} {'month':<8} {'rows':>8} {'total_cost':>14}")
for r in rows:
    print(f"  {r.provider:<7} {r.ds_id:>4} {(r.name or '')[:25]:<25} {r.mon:<8} {r.rows:>8} {float(r.total_cost or 0):>14.2f}")

print("\n=== (2) ocid-20260212 April daily totals (should match BQ) ===")
rows = run("""
    SELECT date, SUM(cost) AS c
    FROM billing_data
    WHERE project_id = 'ocid-20260212' AND date BETWEEN '2026-04-01' AND '2026-04-16'
    GROUP BY date ORDER BY date
""")
for r in rows:
    print(f"  {r.date}  {float(r.c):.4f}")

print("\n=== (3) cb_export / px_billing pre-April: does DB match the CSV import? ===")
# these sources have BQ data only from ~2026-04-10; any earlier data came from CSV.
# Look for Feb/Mar totals — if non-zero, they came from CSV import.
rows = run("""
    SELECT ds.id, ds.name, TO_CHAR(bd.date, 'YYYY-MM') AS mon,
           COUNT(*) AS rows, SUM(bd.cost) AS total_cost,
           MIN(bd.date) AS first_date, MAX(bd.date) AS last_date
    FROM billing_data bd JOIN data_sources ds ON ds.id = bd.data_source_id
    WHERE ds.id IN (5, 6) AND bd.date < '2026-04-01'
    GROUP BY ds.id, ds.name, mon ORDER BY ds.id, mon
""")
for r in rows:
    print(f"  ds={r.id} {r.name:<20} {r.mon}  rows={r.rows:>6}  cost={float(r.total_cost or 0):>12.2f}  range=[{r.first_date}..{r.last_date}]")

print("\n=== (4) Sync_logs: failed runs in last 7 days ===")
rows = run("""
    SELECT sl.id, sl.data_source_id, ds.name, sl.query_start_date, sl.query_end_date,
           sl.records_fetched, sl.records_upserted, sl.status,
           COALESCE(SUBSTRING(sl.error_message FOR 200), '') AS err
    FROM sync_logs sl JOIN data_sources ds ON ds.id = sl.data_source_id
    WHERE sl.start_time >= NOW() - INTERVAL '7 days' AND sl.status != 'success'
    ORDER BY sl.start_time DESC LIMIT 20
""")
for r in rows:
    print(f"  log={r.id} ds={r.data_source_id} {r.name[:25]:<25} {r.query_start_date}..{r.query_end_date} status={r.status} err={r.err[:100]}")

print("\n=== (5) Suspect: keys with identical dedup where cost is suspiciously round/low ===")
# Not really needed — the fix + backfill replaced all GCP/Azure rows via ON CONFLICT DO UPDATE.
# But spot-check top projects in April.
rows = run("""
    SELECT ca.provider, bd.project_id, SUM(bd.cost) AS total
    FROM billing_data bd JOIN data_sources ds ON ds.id = bd.data_source_id
    JOIN cloud_accounts ca ON ca.id = ds.cloud_account_id
    WHERE bd.date BETWEEN '2026-04-01' AND '2026-04-16'
    GROUP BY ca.provider, bd.project_id
    ORDER BY total DESC LIMIT 10
""")
for r in rows:
    print(f"  {r.provider:<7} {r.project_id:<40} ${float(r.total or 0):>12.2f}")
