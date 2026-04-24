"""DRY RUN — compute the delta if we:
  1) DELETE from ds#5/6 the CSV-imported rows (created_at = 2026-04-15 18:51:09.991957)
  2) Re-backfill ds#7 from BQ native 01186D (Oct 30 2025 - Mar 23 2026)
     + BQ xmblilb (Mar 24 - Apr 3 2026) to cover CSV's Apr tail

READ-ONLY. Prints what would change, does not execute."""
import psycopg2, json
from decimal import Decimal
from collections import defaultdict
from google.cloud import bigquery
from google.oauth2 import service_account

CSV_BATCH_TS = '2026-04-15 18:51:09.991957'  # exact CSV-import timestamp

c = psycopg2.connect(host="dataope.postgres.database.azure.com", port=5432, user="azuredb",
                     password="h13nYoFJX6QrfLzB8bdipEUCjsZq2P7W", dbname="cloudcost",
                     sslmode="require", connect_timeout=60)
c.set_session(readonly=True); cur = c.cursor()
def sep(t): print("\n" + "="*80 + "\n  " + t + "\n" + "="*80)

# =========================================================================
# Step 0: Identify ALL CSV-imported rows (not just hash/null — also real-id ones)
# =========================================================================
sep("0. CSV-imported rows (batch timestamp = %s)" % CSV_BATCH_TS)
cur.execute("""
SELECT data_source_id,
       CASE WHEN project_id IS NULL THEN 'NULL'
            WHEN project_id ~ '^project-[0-9a-f]{12}$' THEN 'HASH'
            ELSE 'REAL' END kind,
       COUNT(*) n, ROUND(SUM(cost)::numeric,2) c,
       MIN(date), MAX(date)
FROM billing_data
WHERE data_source_id IN (5,6) AND created_at = %s
GROUP BY data_source_id, kind ORDER BY data_source_id, kind
""", (CSV_BATCH_TS,))
csv_rows_total_ds5 = Decimal("0"); csv_rows_total_ds6 = Decimal("0")
csv_n_ds5 = 0; csv_n_ds6 = 0
for r in cur.fetchall():
    print(f"  ds={r[0]}  kind={r[1]:<5}  rows={r[2]:>6,}  cost=${str(r[3])}  dates={r[4]}~{r[5]}")
    if r[0] == 5: csv_rows_total_ds5 += Decimal(str(r[3])); csv_n_ds5 += r[2]
    else:         csv_rows_total_ds6 += Decimal(str(r[3])); csv_n_ds6 += r[2]

print(f"\n  ds#5 total rows to DELETE: {csv_n_ds5:,}  cost=${csv_rows_total_ds5}")
print(f"  ds#6 total rows to DELETE: {csv_n_ds6:,}  cost=${csv_rows_total_ds6}")

# Also break down by MONTH to show the delete impact
cur.execute("""
SELECT data_source_id, TO_CHAR(DATE_TRUNC('month',date),'YYYY-MM') mon,
       COUNT(*) n, ROUND(SUM(cost)::numeric,2) c
FROM billing_data
WHERE data_source_id IN (5,6) AND created_at = %s
GROUP BY data_source_id, mon ORDER BY data_source_id, mon
""", (CSV_BATCH_TS,))
print("\n  Delete breakdown by month:")
for r in cur.fetchall(): print(f"    ds={r[0]}  {r[1]}  rows={r[2]:>5,}  cost=${r[3]}")

# =========================================================================
# Step 1: What's currently in ds#7 (backfill target)?
# =========================================================================
sep("1. Current ds#7 state in DB (we'd be writing into this)")
cur.execute("""
SELECT COUNT(*), ROUND(SUM(cost)::numeric,2), MIN(date), MAX(date)
FROM billing_data WHERE data_source_id = 7
""")
r = cur.fetchone()
print(f"  ds#7 current: rows={r[0] or 0:,}  cost=${r[1] or 0}  dates={r[2]}~{r[3]}")

cur.execute("""
SELECT TO_CHAR(DATE_TRUNC('month',date),'YYYY-MM') mon,
       COUNT(*) n, ROUND(SUM(cost)::numeric,2) c
FROM billing_data WHERE data_source_id = 7
GROUP BY mon ORDER BY mon
""")
print("  ds#7 by month:")
for r in cur.fetchall(): print(f"    {r[0]}  rows={r[1]:>7,}  cost=${r[2]}")

# =========================================================================
# Step 2: Simulate what backfill from BQ native would produce for ds#7
#         Use the same SQL as gcp_collector.py (GROUP BY the dedup key)
# =========================================================================
sep("2. What BQ native 01186D + xmblilb would produce as ds#7 backfill")

SA = "c:/Users/陈晨/Desktop/工单相关/newgongdan/cloudcost/xmagnet-c0e170e58dc3.json"
creds = service_account.Credentials.from_service_account_info(
    json.load(open(SA)), scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
bq = bigquery.Client(credentials=creds, project=creds.project_id)

# Mimic gcp_collector.py aggregation exactly:
#   SELECT DATE(usage_start_time), project.id, service, sku, IFNULL(location.region,'global'),
#          SUM(cost_at_list), SUM(usage.amount_in_pricing_unit)
#   FROM table
#   GROUP BY billed_date, project_id, service, sku, region
# So rows after collector == distinct (date, pid, service, sku, region) combos.

q_native = """
SELECT
  FORMAT_DATE('%Y-%m', DATE(usage_start_time)) mon,
  COUNT(DISTINCT CONCAT(CAST(DATE(usage_start_time) AS STRING), '|',
                        IFNULL(project.id,'NULL'), '|',
                        IFNULL(service.description,'NULL'), '|',
                        IFNULL(sku.description,'NULL'), '|',
                        IFNULL(location.region,'global'))) n_rows,
  ROUND(SUM(cost_at_list), 2) total
FROM `xmagnet.spaceone_billing_data_us.gcp_billing_export_v1_01186D_EC0E18_F83B2B`
WHERE DATE(usage_start_time) BETWEEN '2025-10-01' AND '2026-04-03'
GROUP BY mon ORDER BY mon
"""
print("  From native 01186D (2025-10-01 ~ 2026-04-03):")
native = {}
for r in bq.query(q_native).result():
    print(f"    {r.mon}  rows={r.n_rows:>7,}  cost=${float(r.total or 0):>13,.2f}")
    native[r.mon] = (r.n_rows, Decimal(str(r.total or 0)))

# Native only has data to 3-23. For 3-24 ~ 4-03 need xmblilb
q_xmblilb = """
SELECT
  FORMAT_DATE('%Y-%m', DATE(usage_start_time)) mon,
  COUNT(DISTINCT CONCAT(CAST(DATE(usage_start_time) AS STRING), '|',
                        IFNULL(project.id,'NULL'), '|',
                        IFNULL(service.description,'NULL'), '|',
                        IFNULL(sku.description,'NULL'), '|',
                        IFNULL(location.region,'global'))) n_rows,
  ROUND(SUM(cost_at_list), 2) total
FROM `xmagnet.xmblilb.gcp_billing_export_v1_01186D_EC0E18_F83B2B`
WHERE DATE(usage_start_time) BETWEEN '2026-03-24' AND '2026-04-03'
GROUP BY mon ORDER BY mon
"""
print("\n  From xmblilb (3-24 ~ 4-03, to cover the gap after native stops):")
xmblilb = {}
for r in bq.query(q_xmblilb).result():
    print(f"    {r.mon}  rows={r.n_rows:>7,}  cost=${float(r.total or 0):>13,.2f}")
    xmblilb[r.mon] = (r.n_rows, Decimal(str(r.total or 0)))

# Total backfill estimate
total_backfill_cost = sum(v[1] for v in native.values()) + sum(v[1] for v in xmblilb.values())
total_backfill_rows = sum(v[0] for v in native.values()) + sum(v[0] for v in xmblilb.values())
print(f"\n  ==> New ds#7 backfill total: ~{total_backfill_rows:,} rows  ~${float(total_backfill_cost):,.2f}")

# =========================================================================
# Step 3: Net effect per month (GCP total)
# =========================================================================
sep("3. Net effect: before vs after, GCP totals by month")
# Current GCP cost by month
cur.execute("""
SELECT TO_CHAR(DATE_TRUNC('month',date),'YYYY-MM') mon,
       ROUND(SUM(cost)::numeric,2) c
FROM billing_data WHERE provider='gcp'
GROUP BY mon ORDER BY mon
""")
cur_gcp = {r[0]: Decimal(str(r[1] or 0)) for r in cur.fetchall()}

# Delete map (ds5/6 by month)
cur.execute("""
SELECT TO_CHAR(DATE_TRUNC('month',date),'YYYY-MM') mon, ROUND(SUM(cost)::numeric,2) c
FROM billing_data WHERE data_source_id IN (5,6) AND created_at = %s
GROUP BY mon ORDER BY mon
""", (CSV_BATCH_TS,))
delete_map = {r[0]: Decimal(str(r[1] or 0)) for r in cur.fetchall()}

# Add map = native ∪ xmblilb
add_map = defaultdict(lambda: Decimal("0"))
for m, (_, v) in native.items(): add_map[m] += v
for m, (_, v) in xmblilb.items(): add_map[m] += v

all_months = sorted(set(cur_gcp) | set(delete_map) | set(add_map))
print(f"  {'month':<8} {'current':>14} {'-delete':>12} {'+add':>14} {'result':>14}")
sum_cur = sum_after = Decimal("0")
for m in all_months:
    cc = cur_gcp.get(m, Decimal("0"))
    dd = delete_map.get(m, Decimal("0"))
    aa = add_map.get(m, Decimal("0"))
    res = cc - dd + aa
    sum_cur += cc; sum_after += res
    print(f"  {m:<8} {float(cc):>14,.2f} {float(dd):>12,.2f} {float(aa):>14,.2f} {float(res):>14,.2f}")
print(f"  {'TOTAL':<8} {float(sum_cur):>14,.2f} {'':<12} {'':<14} {float(sum_after):>14,.2f}")
print(f"\n  Net GCP cost change: +${float(sum_after - sum_cur):,.2f}  (add is positive)")

c.close()
