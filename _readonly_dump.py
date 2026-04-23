"""READ-ONLY: dump billing_data + sync_logs for GCP ds. No writes."""
import os, psycopg2
url = (os.environ.get("SYNC_DATABASE_URL") or os.environ["DATABASE_URL"]).replace("postgresql+psycopg2://","postgresql://").replace("postgresql+asyncpg://","postgresql://")
c = psycopg2.connect(url); c.set_session(readonly=True); cur = c.cursor()

print("### A. data_sources (gcp account=3) ###")
cur.execute("SELECT id, name, is_active, sync_status, last_sync_at, config FROM data_sources WHERE cloud_account_id=3 ORDER BY id")
for r in cur.fetchall(): print(r)

print("\n### B. billing_data per ds per month ###")
cur.execute("""
SELECT data_source_id, TO_CHAR(DATE_TRUNC('month',date),'YYYY-MM') mon,
       COUNT(*) rows, ROUND(SUM(cost)::numeric,2) total_cost, MIN(date) mn, MAX(date) mx
FROM billing_data WHERE provider='gcp'
GROUP BY data_source_id, mon ORDER BY data_source_id, mon""")
for r in cur.fetchall(): print(r)

print("\n### C. sync_logs last 30 entries for ds 3,4,5,6,7 ###")
cur.execute("""
SELECT data_source_id, status, start_time, end_time, query_start_date, query_end_date,
       records_fetched, records_upserted, LEFT(COALESCE(error_message,''),120) err
FROM sync_logs WHERE data_source_id IN (3,4,5,6,7)
ORDER BY start_time DESC LIMIT 30""")
for r in cur.fetchall(): print(r)

print("\n### D. billing_data top 5 projects per ds (Apr 2026) ###")
cur.execute("""
SELECT data_source_id, project_id, COUNT(*) rows, ROUND(SUM(cost)::numeric,2) total_cost
FROM billing_data WHERE provider='gcp' AND date >= '2026-04-01' AND date <= '2026-04-22'
GROUP BY data_source_id, project_id ORDER BY data_source_id, total_cost DESC NULLS LAST""")
for r in cur.fetchall(): print(r)

c.close()
