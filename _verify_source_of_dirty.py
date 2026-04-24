"""Verify: are ALL hash/NULL rows 100% from CSV import (not from BQ sync)?

Strategy:
  1. For every hash/NULL row, inspect created_at timestamp
  2. Compare with sync_logs history (when was ds#5/6 ever synced?)
  3. Cross-check CSV row count vs DB hash+NULL row count (shouldn't exceed CSV)
  4. Check if BQ VIEW itself ever returns hash-style project.id (sanity)
Read-only."""
import psycopg2, csv, json
from collections import defaultdict
from decimal import Decimal
from google.cloud import bigquery
from google.oauth2 import service_account

c = psycopg2.connect(host="dataope.postgres.database.azure.com", port=5432, user="azuredb",
                     password="h13nYoFJX6QrfLzB8bdipEUCjsZq2P7W", dbname="cloudcost",
                     sslmode="require", connect_timeout=15)
c.set_session(readonly=True); cur = c.cursor()

def sep(t): print("\n" + "="*78 + "\n  " + t + "\n" + "="*78)

# 1. created_at of every hash / NULL row
sep("1. created_at distribution of DIRTY rows (hash OR null) in ds#5/6")
cur.execute("""
SELECT
  CASE WHEN project_id IS NULL THEN 'NULL'
       WHEN project_id ~ '^project-[0-9a-f]{12}$' THEN 'HASH'
       ELSE 'REAL'
  END AS kind,
  data_source_id,
  DATE(created_at) AS cday,
  COUNT(*) n,
  ROUND(SUM(cost)::numeric,2) c
FROM billing_data
WHERE data_source_id IN (5,6)
GROUP BY kind, data_source_id, cday
ORDER BY kind, data_source_id, cday
""")
for r in cur.fetchall():
    print(f"  kind={r[0]:<5} ds={r[1]} created_day={r[2]}  rows={r[3]:>6,}  cost=${str(r[4])}")

# 2. Earliest + latest sync_logs for ds#5/6 — when has sync touched this ds?
sep("2. sync_logs for ds#5/6 — every successful run's time + result")
cur.execute("""
SELECT id, data_source_id, status, start_time, end_time, records_fetched,
       query_start_date, query_end_date
FROM sync_logs WHERE data_source_id IN (5,6)
ORDER BY start_time ASC LIMIT 30
""")
all_syncs = cur.fetchall()
for r in all_syncs: print(f"  id={r[0]} ds={r[1]} {r[2]:<8} start={r[3]}  fetched={r[5]}  window={r[6]}~{r[7]}")
print(f"  ... total ds5/6 sync_logs: querying ...")
cur.execute("SELECT data_source_id, COUNT(*) FROM sync_logs WHERE data_source_id IN (5,6) GROUP BY data_source_id")
for r in cur.fetchall(): print(f"  ds={r[0]} total runs = {r[1]}")

# 3. DB row count exactly matches CSV row count per (ds, hash/null)?
sep("3. DB DIRTY rows vs CSV rows — exact correspondence check")
csv_hash_ds5 = defaultdict(lambda: {"n":0, "c":Decimal("0")})
csv_null_ds5 = {"n":0, "c":Decimal("0")}
with open("cost_before_2026-04-03_cb_export_like.csv", encoding="utf-8-sig") as f:
    for row in csv.DictReader(f):
        pid = row["project_id"].strip()
        cost = Decimal(row["cost"] or "0")
        if pid == "":
            csv_null_ds5["n"] += 1
            csv_null_ds5["c"] += cost
        elif pid.startswith("project-") and len(pid) == 20 and all(c in "0123456789abcdef" for c in pid[8:]):
            csv_hash_ds5[pid]["n"] += 1
            csv_hash_ds5[pid]["c"] += cost

csv_hash_ds6 = defaultdict(lambda: {"n":0, "c":Decimal("0")})
csv_null_ds6 = {"n":0, "c":Decimal("0")}
with open("cost_before_2026-04-03_px_billing_like.csv", encoding="utf-8-sig") as f:
    for row in csv.DictReader(f):
        pid = row["project_id"].strip()
        cost = Decimal(row["cost"] or "0")
        if pid == "":
            csv_null_ds6["n"] += 1
            csv_null_ds6["c"] += cost
        elif pid.startswith("project-") and len(pid) == 20 and all(c in "0123456789abcdef" for c in pid[8:]):
            csv_hash_ds6[pid]["n"] += 1
            csv_hash_ds6[pid]["c"] += cost

print(f"  CSV cb_export (ds#5): hash raw-rows total = {sum(v['n'] for v in csv_hash_ds5.values())}, null raw-rows = {csv_null_ds5['n']}")
print(f"  CSV px_billing (ds#6): hash raw-rows total = {sum(v['n'] for v in csv_hash_ds6.values())}, null raw-rows = {csv_null_ds6['n']}")
# note: CSV rows get consolidated by ON CONFLICT DO UPDATE (+sum) to DB dedup key, so DB rows <= CSV rows

cur.execute("""
SELECT data_source_id, COUNT(*) FILTER (WHERE project_id IS NULL) null_n,
       COUNT(*) FILTER (WHERE project_id ~ '^project-[0-9a-f]{12}$') hash_n,
       ROUND(SUM(cost) FILTER (WHERE project_id IS NULL)::numeric,2) null_c,
       ROUND(SUM(cost) FILTER (WHERE project_id ~ '^project-[0-9a-f]{12}$')::numeric,2) hash_c
FROM billing_data WHERE data_source_id IN (5,6)
GROUP BY data_source_id ORDER BY data_source_id
""")
print("\n  DB side (consolidated by unique-key):")
for r in cur.fetchall():
    print(f"    ds={r[0]} null_rows={r[1]}  null_cost=${r[3]}   hash_rows={r[2]}  hash_cost=${r[4]}")

# 4. Cost must exactly match CSV sums (both sides aggregated by project_id)
sep("4. Per-hash-project cost comparison: CSV raw vs DB consolidated")
cur.execute("""
SELECT project_id, ROUND(SUM(cost)::numeric,2) c, COUNT(*) n
FROM billing_data WHERE data_source_id=5 AND project_id ~ '^project-[0-9a-f]{12}$'
GROUP BY project_id ORDER BY c DESC
""")
db_hash_ds5 = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
print(f"  {'project_id':<30} {'CSV cost':>12} {'DB cost':>12} {'diff':>8}")
for pid, v in sorted(csv_hash_ds5.items(), key=lambda kv: -kv[1]["c"]):
    db_c, db_n = db_hash_ds5.get(pid, (Decimal("0"), 0))
    diff = Decimal(str(db_c)) - v["c"]
    flag = "" if abs(diff) < Decimal("0.01") else "  ← MISMATCH"
    print(f"  {pid:<30} {float(v['c']):>12,.2f} {float(db_c):>12,.2f} {float(diff):>8,.2f}{flag}")

# 5. Does BQ VIEW EVER return a hash-style project.id?  (should be NO)
sep("5. Does BQ VIEW / native return any hash-style project.id?")
SA = "c:/Users/陈晨/Desktop/工单相关/newgongdan/cloudcost/xmagnet-c0e170e58dc3.json"
creds = service_account.Credentials.from_service_account_info(
    json.load(open(SA)), scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
bq = bigquery.Client(credentials=creds, project=creds.project_id)
for fqt in ["cb-export.other.xm", "px-billing-report.other.xm",
            "xmagnet.spaceone_billing_data_us.gcp_billing_export_v1_01186D_EC0E18_F83B2B"]:
    try:
        r = list(bq.query(f"""
            SELECT COUNT(DISTINCT project.id) total_pid,
                   COUNT(DISTINCT CASE WHEN REGEXP_CONTAINS(project.id, r'^project-[0-9a-f]{{12}}$')
                                       THEN project.id END) hash_pid
            FROM `{fqt}` WHERE DATE(usage_start_time) >= '2025-10-01'
        """).result())[0]
        print(f"  {fqt}: distinct pids={r.total_pid}, hash-style pids={r.hash_pid}")
    except Exception as e:
        print(f"  {fqt}: error {str(e)[:80]}")

# 6. Any hash rows have created_at AFTER 2026-04-10 (i.e. after CSV import likely done)?
sep("6. Hash/NULL rows with created_at AFTER 2026-04-10 (would indicate non-CSV source)")
cur.execute("""
SELECT data_source_id,
       CASE WHEN project_id IS NULL THEN 'NULL'
            WHEN project_id ~ '^project-[0-9a-f]{12}$' THEN 'HASH' END kind,
       MIN(created_at) earliest, MAX(created_at) latest, COUNT(*) n
FROM billing_data
WHERE data_source_id IN (5,6)
  AND (project_id IS NULL OR project_id ~ '^project-[0-9a-f]{12}$')
GROUP BY data_source_id, kind ORDER BY data_source_id, kind
""")
for r in cur.fetchall():
    print(f"  ds={r[0]} kind={r[1]:<5} earliest={r[2]}  latest={r[3]}  n={r[4]}")

# Also check: any row in ds#5/6 with created_at AFTER 2026-04-10 that's NOT hash/null?
cur.execute("""
SELECT data_source_id, COUNT(*) n, MIN(created_at), MAX(created_at)
FROM billing_data WHERE data_source_id IN (5,6) AND created_at >= '2026-04-10'
GROUP BY data_source_id
""")
print("\n  for comparison — rows with created_at >= 2026-04-10 (must be from sync not CSV):")
for r in cur.fetchall(): print(f"  ds={r[0]}  n={r[1]:,}  range={r[2]}~{r[3]}")

c.close()
