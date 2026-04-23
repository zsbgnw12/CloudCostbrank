"""Two tasks:
1. CSV project_name → BQ project.id mapping (to resolve the 'project-{hash}' anonymization)
2. Explain ds#3 missing 1/1 - 3/23 data — look at created_at / sync_logs / BQ history
Read-only."""
import csv, json, psycopg2
from collections import defaultdict
from decimal import Decimal

# -------- CSV: project_id ↔ project_name pairs for the hashed ones --------
print("### 1. CSV project-{hash} rows: their project_name column values ###")
hashed_names = defaultdict(lambda: {"name": set(), "cost": Decimal("0"), "n": 0, "mons": set()})
with open("cost_before_2026-04-03_cb_export_like.csv", encoding="utf-8-sig") as f:
    r = csv.DictReader(f)
    for row in r:
        pid = row["project_id"].strip()
        if pid.startswith("project-") or pid == "":
            hashed_names[pid]["name"].add(row["project_name"].strip())
            hashed_names[pid]["cost"] += Decimal(row["cost"] or "0")
            hashed_names[pid]["n"] += 1
            hashed_names[pid]["mons"].add(row["billed_month"])

print(f"{'hash project_id':<30} {'project_name(s)':<45} {'cost':>12}  months")
for pid, info in sorted(hashed_names.items(), key=lambda kv: -kv[1]["cost"]):
    names = ",".join(sorted(info["name"]))[:45]
    print(f"  {pid:<30} {names:<45} {float(info['cost']):>11,.2f}  {','.join(sorted(info['mons']))}")

# Compare that set of names with BQ native 01186D's project.name values
print("\n### 2. BQ native 01186D: distinct project.name values (2025-10+) ###")
from google.cloud import bigquery
from google.oauth2 import service_account
SA = "c:/Users/陈晨/Desktop/工单相关/newgongdan/cloudcost/xmagnet-c0e170e58dc3.json"
creds = service_account.Credentials.from_service_account_info(
    json.load(open(SA)), scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
client = bigquery.Client(credentials=creds, project=creds.project_id)

q = """
SELECT project.id pid, project.name pname, COUNT(*) n, SUM(cost_at_list) c,
       FORMAT_DATE('%Y-%m', DATE(MIN(usage_start_time))) mn,
       FORMAT_DATE('%Y-%m', DATE(MAX(usage_start_time))) mx
FROM `xmagnet.spaceone_billing_data_us.gcp_billing_export_v1_01186D_EC0E18_F83B2B`
WHERE DATE(usage_start_time) >= '2025-10-01'
GROUP BY pid, pname
ORDER BY c DESC NULLS LAST
"""
bq_rows = list(client.query(q).result())
print(f"  {'project.id':<35} {'project.name':<30} {'cost':>12}  months")
for r in bq_rows:
    print(f"  {r.pid!r:<35} {r.pname!r:<30} {float(r.c or 0):>11,.2f}  {r.mn}~{r.mx}")

# Same for VIEW cb-export
print("\n### 3. BQ cb-export.other.xm VIEW: distinct project.id + project.name ###")
q2 = """
SELECT project.id pid, project.name pname, COUNT(*) n, SUM(cost) c,
       FORMAT_DATE('%Y-%m', DATE(MIN(usage_start_time))) mn,
       FORMAT_DATE('%Y-%m', DATE(MAX(usage_start_time))) mx
FROM `cb-export.other.xm`
WHERE DATE(usage_start_time) >= '2025-10-01'
GROUP BY pid, pname
ORDER BY c DESC NULLS LAST
"""
for r in client.query(q2).result():
    print(f"  {r.pid!r:<35} {r.pname!r:<30} {float(r.c or 0):>11,.2f}  {r.mn}~{r.mx}")

# -------- DB: ds#3 history investigation --------
print("\n### 4. ds#3 creation + sync history ###")
c = psycopg2.connect(host="dataope.postgres.database.azure.com", port=5432, user="azuredb",
                     password="h13nYoFJX6QrfLzB8bdipEUCjsZq2P7W", dbname="cloudcost",
                     sslmode="require", connect_timeout=15)
c.set_session(readonly=True); cur = c.cursor()

cur.execute("""
SELECT id, name, cloud_account_id, is_active, sync_status, last_sync_at, config
FROM data_sources WHERE id = 3""")
print("  data_source #3:")
for r in cur.fetchall(): print(f"    {r}")

cur.execute("""
SELECT id, name, provider, created_at FROM cloud_accounts WHERE id = 3""")
print("\n  cloud_account #3:")
for r in cur.fetchall(): print(f"    {r}")

cur.execute("""
SELECT id, status, start_time, end_time, query_start_date, query_end_date,
       records_fetched, records_upserted, LEFT(COALESCE(error_message,''),100) err
FROM sync_logs WHERE data_source_id = 3
ORDER BY start_time ASC LIMIT 15""")
print("\n  first 15 sync_logs of ds#3:")
for r in cur.fetchall(): print(f"    id={r[0]} {r[1]} start={r[2]} window={r[4]}~{r[5]} fetched={r[6]} upserted={r[7]}  err={r[8]!r}")

cur.execute("""
SELECT created_at
FROM billing_data WHERE data_source_id = 3 ORDER BY created_at ASC LIMIT 1""")
print("\n  earliest billing_data row for ds#3 created_at:")
for r in cur.fetchall(): print(f"    {r}")

c.close()

# BQ: does the underlying table have data before 2026-03-24?
print("\n### 5. BQ xmind VIEW — what's the earliest date available? ###")
q3 = """
SELECT FORMAT_DATE('%Y-%m', DATE(usage_start_time)) mon, COUNT(*) n, SUM(cost_at_list) c
FROM `share-service-nonprod.xmind.billing_report`
GROUP BY mon ORDER BY mon
"""
try:
    for r in client.query(q3).result():
        print(f"  {r.mon}  rows={r.n:>8,}  cost=${float(r.c or 0):>13,.2f}")
except Exception as e:
    print(f"  error: {e}")
