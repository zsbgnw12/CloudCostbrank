import os, psycopg2, json
url = (os.environ.get("SYNC_DATABASE_URL") or os.environ["DATABASE_URL"]).replace("postgresql+psycopg2://","postgresql://").replace("postgresql+asyncpg://","postgresql://")
c = psycopg2.connect(url); cur = c.cursor()

print("=== sync_logs last 20 rows for GCP ds (3,4,5,6,7) ===")
cur.execute("""
SELECT data_source_id, status, start_time, end_time,
       query_start_date, query_end_date, records_fetched, records_upserted,
       LEFT(COALESCE(error_message,''),140) as err
FROM sync_logs
WHERE data_source_id IN (3,4,5,6,7)
ORDER BY start_time DESC LIMIT 25
""")
for r in cur.fetchall(): print(r)

print("\n=== billing_data per ds (gcp) — rows + cost by month ===")
cur.execute("""
SELECT data_source_id, TO_CHAR(DATE_TRUNC('month', date),'YYYY-MM') mon,
       COUNT(*) rows, ROUND(SUM(cost)::numeric,2) total_cost
FROM billing_data
WHERE provider='gcp'
GROUP BY data_source_id, mon
ORDER BY data_source_id, mon
""")
for r in cur.fetchall(): print(r)

print("\n=== data_sources.sync_status + last_sync_at for gcp ===")
cur.execute("""
SELECT id, name, is_active, sync_status, last_sync_at
FROM data_sources WHERE cloud_account_id=3 ORDER BY id
""")
for r in cur.fetchall(): print(r)

print("\n=== any other source with overlapping billing_account? ===")
cur.execute("""
SELECT id, name, config->>'billing_account_id' ba, config->>'project_id' pid, is_active
FROM data_sources
WHERE config ? 'billing_account_id'
ORDER BY ba
""")
for r in cur.fetchall(): print(r)
