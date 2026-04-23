"""READ-ONLY verification of 3 claims. No writes."""
import os, psycopg2
c = psycopg2.connect(host="dataope.postgres.database.azure.com", port=5432, user="azuredb", password="h13nYoFJX6QrfLzB8bdipEUCjsZq2P7W", dbname="cloudcost", sslmode="require", connect_timeout=15)
c.set_session(readonly=True); cur = c.cursor()

print("### 1. currency distribution in billing_data (2026-01-01+) ###")
cur.execute("""
SELECT provider, currency, COUNT(*), ROUND(SUM(cost)::numeric, 2) AS total
FROM billing_data
WHERE date >= '2026-01-01'
GROUP BY provider, currency ORDER BY provider, currency""")
for r in cur.fetchall(): print(f"  provider={r[0]}  currency={r[1]!r}  rows={r[2]:,}  total={r[3]}")

print("\n### 2. region=NULL rows (should be 0 after consolidate_null_region fix) ###")
cur.execute("""
SELECT provider, data_source_id, COUNT(*), ROUND(SUM(cost)::numeric, 2)
FROM billing_data
WHERE region IS NULL AND date >= '2026-01-01'
GROUP BY provider, data_source_id ORDER BY provider, data_source_id""")
rows = cur.fetchall()
if not rows: print("  (none — clean)")
for r in rows: print(f"  {r}")

print("\n### 3. alert_rules by threshold_type ###")
cur.execute("""
SELECT threshold_type, COUNT(*) total, SUM(CASE WHEN is_active THEN 1 ELSE 0 END) active
FROM alert_rules GROUP BY threshold_type ORDER BY total DESC""")
rows = cur.fetchall()
if not rows: print("  (no alert rules configured at all)")
for r in rows: print(f"  {r[0]}: total={r[1]}  active={r[2]}")

print("\n### 4. billing_data per ds per month (GCP) — ground truth we've been asking for ###")
cur.execute("""
SELECT data_source_id, TO_CHAR(DATE_TRUNC('month',date),'YYYY-MM') mon,
       COUNT(*) rows, ROUND(SUM(cost)::numeric,2) total, MIN(date) mn, MAX(date) mx
FROM billing_data WHERE provider='gcp' AND date >= '2026-01-01'
GROUP BY data_source_id, mon ORDER BY data_source_id, mon""")
for r in cur.fetchall(): print(f"  ds={r[0]} {r[1]}: rows={r[2]:>7,} total=${r[3]:>12} {r[4]}~{r[5]}")

print("\n### 5. sync_logs last 12 GCP entries ###")
cur.execute("""
SELECT data_source_id, status, start_time, records_fetched, records_upserted,
       query_start_date, query_end_date, LEFT(COALESCE(error_message,''),100) err
FROM sync_logs WHERE data_source_id IN (3,4,5,6,7)
ORDER BY start_time DESC LIMIT 12""")
for r in cur.fetchall(): print(f"  ds={r[0]} {r[1]} start={r[2]} fetched={r[3]} upserted={r[4]} window={r[5]}~{r[6]} err={r[7]!r}")

print("\n### 6. projects — duplicate external_project_id across supply_sources? ###")
cur.execute("""
SELECT external_project_id, COUNT(DISTINCT supply_source_id) nsrc, COUNT(*) nrows
FROM projects
GROUP BY external_project_id HAVING COUNT(DISTINCT supply_source_id) > 1
LIMIT 20""")
rows = cur.fetchall()
if not rows: print("  (none — every ext_id lives in exactly one supply_source)")
for r in rows: print(f"  {r}")

c.close()
