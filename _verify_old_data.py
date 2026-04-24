"""Verify: do CSV-imported REAL-id rows (2025-10 ~ 2026-02, i.e. 'old data')
match BQ native 01186D for same project + month?

This is the question: is the historical backfill accurate or subtly off?
Read-only."""
import psycopg2, json
from collections import defaultdict
from decimal import Decimal
from google.cloud import bigquery
from google.oauth2 import service_account

SA = "c:/Users/陈晨/Desktop/工单相关/newgongdan/cloudcost/xmagnet-c0e170e58dc3.json"
creds = service_account.Credentials.from_service_account_info(
    json.load(open(SA)), scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
bq = bigquery.Client(credentials=creds, project=creds.project_id)

c = psycopg2.connect(host="dataope.postgres.database.azure.com", port=5432, user="azuredb",
                     password="h13nYoFJX6QrfLzB8bdipEUCjsZq2P7W", dbname="cloudcost",
                     sslmode="require", connect_timeout=60)
c.set_session(readonly=True); cur = c.cursor()

# 1. DB ds#5 REAL rows: per (project_id, month) cost — CSV imported historical
print("=" * 80)
print("1. DB ds#5 REAL-id rows (2025-10 ~ 2026-04-03 range, the CSV historical data)")
print("=" * 80)
cur.execute("""
SELECT project_id, TO_CHAR(DATE_TRUNC('month',date),'YYYY-MM') mon,
       ROUND(SUM(cost)::numeric,2) c, COUNT(*) n
FROM billing_data
WHERE data_source_id=5
  AND project_id IS NOT NULL
  AND project_id !~ '^project-[0-9a-f]{12}$'
  AND date <= '2026-04-03'  -- only the window CSV covers
GROUP BY project_id, mon
ORDER BY project_id, mon
""")
db_real = defaultdict(dict)
for r in cur.fetchall():
    db_real[r[0]][r[1]] = (Decimal(str(r[2])), r[3])

# 2. BQ native 01186D: same projects + months
print("2. BQ native 01186D (the source-of-truth): same projects + months\n")
q = """
SELECT project.id pid, FORMAT_DATE('%Y-%m', DATE(usage_start_time)) mon,
       ROUND(SUM(cost), 2) c, COUNT(*) n
FROM `xmagnet.spaceone_billing_data_us.gcp_billing_export_v1_01186D_EC0E18_F83B2B`
WHERE DATE(usage_start_time) BETWEEN '2025-10-01' AND '2026-04-03'
GROUP BY pid, mon
"""
bq_data = defaultdict(dict)
for row in bq.query(q).result():
    bq_data[row.pid][row.mon] = (Decimal(str(row.c or 0)), row.n)

# 3. Compare
all_pids = sorted(set(db_real.keys()) | set(bq_data.keys()))
print(f"{'project':<30} {'month':<8} {'DB(CSV)':>13} {'BQ native':>13} {'diff':>10}")
total_db = Decimal("0"); total_bq = Decimal("0"); mismatches = 0
for pid in all_pids:
    all_mons = sorted(set(db_real[pid].keys()) | set(bq_data[pid].keys()))
    for mon in all_mons:
        db_c, _ = db_real[pid].get(mon, (Decimal("0"), 0))
        bq_c, _ = bq_data[pid].get(mon, (Decimal("0"), 0))
        diff = db_c - bq_c
        total_db += db_c; total_bq += bq_c
        if abs(diff) >= Decimal("0.01"):
            mismatches += 1
            flag = "  ← DIFF"
        else:
            flag = ""
        print(f"  {pid:<30} {mon:<8} {float(db_c):>13,.2f} {float(bq_c):>13,.2f} {float(diff):>10,.2f}{flag}")

print(f"\n{'TOTAL':<30} {'':<8} {float(total_db):>13,.2f} {float(total_bq):>13,.2f} {float(total_db - total_bq):>10,.2f}")
print(f"\n  mismatches >= $0.01: {mismatches}")

# 4. Also: total CSV vs BQ when comparing full-population sum (including hash rows cast to their real name)
print("\n" + "=" * 80)
print("4. If we fold hash IDs back to real project_id, does DB total match BQ total by month?")
print("=" * 80)
cur.execute("""
SELECT TO_CHAR(DATE_TRUNC('month',date),'YYYY-MM') mon,
       ROUND(SUM(cost)::numeric,2) total_db
FROM billing_data
WHERE data_source_id=5 AND date <= '2026-04-03'
GROUP BY mon ORDER BY mon
""")
db_by_mon = {r[0]: Decimal(str(r[1])) for r in cur.fetchall()}

q2 = """
SELECT FORMAT_DATE('%Y-%m', DATE(usage_start_time)) mon,
       ROUND(SUM(cost), 2) total_bq
FROM `xmagnet.spaceone_billing_data_us.gcp_billing_export_v1_01186D_EC0E18_F83B2B`
WHERE DATE(usage_start_time) BETWEEN '2025-10-01' AND '2026-04-03'
GROUP BY mon ORDER BY mon
"""
bq_by_mon = {row.mon: Decimal(str(row.total_bq or 0)) for row in bq.query(q2).result()}

all_mons = sorted(set(db_by_mon) | set(bq_by_mon))
print(f"  {'month':<10} {'DB ds#5 sum':>13} {'BQ 01186D sum':>15} {'diff':>10}")
for mon in all_mons:
    d = db_by_mon.get(mon, Decimal("0"))
    b = bq_by_mon.get(mon, Decimal("0"))
    print(f"  {mon:<10} {float(d):>13,.2f} {float(b):>15,.2f} {float(d - b):>10,.2f}")

c.close()
