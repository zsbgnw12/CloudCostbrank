"""Check how much data each provider's collector is losing due to UPSERT-over-unagg bug.

Method: for each provider, sync_logs tells us records_fetched (what the collector returned)
vs records_upserted (what actually made it to the DB). If fetched > upserted, the delta is
the number of rows that collided and got overwritten — each collision is lost money.
"""
import os
from pathlib import Path
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
engine = create_engine(os.environ["SYNC_DATABASE_URL"])

with engine.connect() as c:
    print("=== Recent sync_logs (last 5 per data_source, April) ===")
    rows = c.execute(text("""
        SELECT sl.data_source_id, ds.name, ca.provider,
               sl.start_time, sl.query_start_date, sl.query_end_date,
               sl.records_fetched, sl.records_upserted, sl.status
        FROM sync_logs sl
        JOIN data_sources ds ON ds.id = sl.data_source_id
        JOIN cloud_accounts ca ON ca.id = ds.cloud_account_id
        WHERE sl.start_time >= '2026-04-01'
        ORDER BY ca.provider, sl.data_source_id, sl.start_time DESC
    """)).fetchall()

    by_prov = {}
    for r in rows:
        fetched = r.records_fetched or 0
        upserted = r.records_upserted or 0
        lost = fetched - upserted
        pct = (lost / fetched * 100) if fetched else 0
        key = r.provider
        by_prov.setdefault(key, {"fetched": 0, "upserted": 0, "runs": 0})
        by_prov[key]["fetched"] += fetched
        by_prov[key]["upserted"] += upserted
        by_prov[key]["runs"] += 1
        print(f"  [{r.provider}] ds={r.data_source_id} {r.name[:25]:<25} "
              f"range={r.query_start_date}..{r.query_end_date}  "
              f"fetched={fetched:>7} upserted={upserted:>6} lost={lost:>7} ({pct:5.1f}%)  {r.status}")

    print("\n=== Summary by provider (April sync runs) ===")
    print(f"  {'provider':<10} {'runs':>6} {'fetched':>10} {'upserted':>10} {'lost_rows':>10} {'loss%':>8}")
    for prov, s in by_prov.items():
        lost = s["fetched"] - s["upserted"]
        pct = (lost / s["fetched"] * 100) if s["fetched"] else 0
        print(f"  {prov:<10} {s['runs']:>6} {s['fetched']:>10} {s['upserted']:>10} {lost:>10} {pct:>7.1f}%")

    print("\n=== Cross-check: distinct dedup-keys vs row count currently in billing_data (April) ===")
    # row_count == distinct_dedup_keys always (unique constraint), so this just shows
    # how many rows each provider actually has in DB
    rows = c.execute(text("""
        SELECT ca.provider, COUNT(*) AS n_rows, SUM(bd.cost) AS total_cost
        FROM billing_data bd
        JOIN data_sources ds ON ds.id = bd.data_source_id
        JOIN cloud_accounts ca ON ca.id = ds.cloud_account_id
        WHERE bd.date BETWEEN '2026-04-01' AND '2026-04-15'
        GROUP BY ca.provider ORDER BY ca.provider
    """)).fetchall()
    for r in rows:
        print(f"  {r.provider:<10} rows_in_db={r.n_rows:>7}  sum_cost={float(r.total_cost or 0):>12.2f}")
