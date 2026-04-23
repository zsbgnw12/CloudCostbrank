"""Verify CSV vs BQ VIEW correspondence per project per month for ds#5 (cb_export).
User says CSV is historical backfill because BQ table only has incremental data.
Check: do CSV projects match BQ VIEW projects? Is there date/cost overlap that
would indicate double counting?
"""
import csv, json
from collections import defaultdict
from decimal import Decimal
from google.cloud import bigquery
from google.oauth2 import service_account

SA = "c:/Users/陈晨/Desktop/工单相关/newgongdan/cloudcost/xmagnet-c0e170e58dc3.json"
creds = service_account.Credentials.from_service_account_info(
    json.load(open(SA)), scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
client = bigquery.Client(credentials=creds, project=creds.project_id)

# 1. Aggregate CSV by (month, project_id)
print("### CSV (cb_export) — per project per month ###")
csv_agg = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
csv_totals = defaultdict(lambda: Decimal("0"))
with open("cost_before_2026-04-03_cb_export_like.csv", encoding="utf-8-sig") as f:
    r = csv.DictReader(f)
    for row in r:
        mon = (row["billed_month"] or row["billed_date"][:7]).strip()
        pid = row["project_id"].strip()
        cost = Decimal(row["cost"] or "0")
        csv_agg[pid][mon] += cost
        csv_totals[pid] += cost

# 2. BQ VIEW same period, per (month, project.id)
print("### BQ VIEW (cb-export.other.xm) — per project per month (2025-10 ~ 2026-04-22) ###")
q = """
SELECT
  FORMAT_DATE('%Y-%m', DATE(usage_start_time)) mon,
  project.id pid,
  SUM(cost) c,
  COUNT(*) n
FROM `cb-export.other.xm`
WHERE DATE(usage_start_time) BETWEEN '2025-10-01' AND '2026-04-22'
GROUP BY mon, pid
"""
bq_agg = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
bq_totals = defaultdict(lambda: Decimal("0"))
for row in client.query(q).result():
    bq_agg[row.pid][row.mon] += Decimal(str(row.c or 0))
    bq_totals[row.pid] += Decimal(str(row.c or 0))

# Also probe the underlying orphan table in xmagnet for coverage check
print("### BQ orphan xmagnet.spaceone_billing_data_us.cb_export_xm_table — same window ###")
q2 = """
SELECT
  FORMAT_DATE('%Y-%m', DATE(usage_start_time)) mon,
  project.id pid,
  SUM(cost_at_list) c,
  COUNT(*) n
FROM `xmagnet.spaceone_billing_data_us.cb_export_xm_table`
WHERE DATE(usage_start_time) BETWEEN '2025-10-01' AND '2026-04-22'
GROUP BY mon, pid
"""
orphan_agg = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
orphan_totals = defaultdict(lambda: Decimal("0"))
try:
    for row in client.query(q2).result():
        orphan_agg[row.pid][row.mon] += Decimal(str(row.c or 0))
        orphan_totals[row.pid] += Decimal(str(row.c or 0))
except Exception as e:
    print(f"  orphan query error: {e}")

# 3. Also probe 01186D native (what CSV's billing_account claims to be)
print("### BQ ds#7 native xmagnet.spaceone_billing_data_us.gcp_billing_export_v1_01186D ###")
q3 = """
SELECT
  FORMAT_DATE('%Y-%m', DATE(usage_start_time)) mon,
  project.id pid,
  SUM(cost_at_list) c,
  COUNT(*) n
FROM `xmagnet.spaceone_billing_data_us.gcp_billing_export_v1_01186D_EC0E18_F83B2B`
WHERE DATE(usage_start_time) BETWEEN '2025-10-01' AND '2026-04-22'
GROUP BY mon, pid
"""
native_agg = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
native_totals = defaultdict(lambda: Decimal("0"))
for row in client.query(q3).result():
    native_agg[row.pid][row.mon] += Decimal(str(row.c or 0))
    native_totals[row.pid] += Decimal(str(row.c or 0))

# --- Compare: which projects are in CSV vs which in BQ VIEW vs native
csv_pids = set(csv_agg.keys())
view_pids = set(bq_agg.keys())
orphan_pids = set(orphan_agg.keys())
native_pids = set(native_agg.keys())

print(f"\n=== Project set comparison ===")
print(f"  CSV pids: {len(csv_pids)}")
print(f"  BQ VIEW (cb-export.other.xm) pids: {len(view_pids)}")
print(f"  BQ orphan cb_export_xm_table pids: {len(orphan_pids)}")
print(f"  BQ native 01186D pids: {len(native_pids)}")

print(f"\n  CSV ∩ VIEW: {len(csv_pids & view_pids)}  e.g. {list(csv_pids & view_pids)[:8]}")
print(f"  CSV ∩ native(01186D): {len(csv_pids & native_pids)}  e.g. {list(csv_pids & native_pids)[:8]}")
print(f"  CSV \\ (VIEW ∪ orphan ∪ native): {len(csv_pids - view_pids - orphan_pids - native_pids)}")
print(f"    missing from BQ entirely: {sorted(csv_pids - view_pids - orphan_pids - native_pids)}")

# --- For each CSV project, show: CSV total vs BQ (view, orphan, native) total
print(f"\n=== Per-project totals (2025-10 ~ 2026-04-22, all sources) ===")
print(f"{'project_id':<30} {'CSV':>12} {'VIEW':>12} {'orphan':>12} {'native01186D':>14}")
all_pids = sorted(csv_pids | view_pids | orphan_pids | native_pids)
for pid in all_pids:
    csv_t = float(csv_totals.get(pid, 0))
    view_t = float(bq_totals.get(pid, 0))
    orph_t = float(orphan_totals.get(pid, 0))
    nat_t = float(native_totals.get(pid, 0))
    print(f"  {pid:<30} {csv_t:>12,.2f} {view_t:>12,.2f} {orph_t:>12,.2f} {nat_t:>14,.2f}")

# --- Month overlap check for a specific high-cost project
print(f"\n=== Month-by-month for project-affbd6906ac4 (top CSV project $84k in Mar) ===")
for source_name, agg in [("CSV", csv_agg), ("VIEW", bq_agg), ("orphan", orphan_agg), ("native01186D", native_agg)]:
    d = agg.get("project-affbd6906ac4", {})
    if d:
        print(f"  {source_name:<12} " + "  ".join(f"{m}=${float(v):,.0f}" for m, v in sorted(d.items())))
    else:
        print(f"  {source_name:<12} (none)")

print(f"\n=== Month-by-month for chuer-2026021801 (shared between CSV and VIEW?) ===")
for source_name, agg in [("CSV", csv_agg), ("VIEW", bq_agg), ("orphan", orphan_agg), ("native01186D", native_agg)]:
    d = agg.get("chuer-2026021801", {})
    if d:
        print(f"  {source_name:<12} " + "  ".join(f"{m}=${float(v):,.0f}" for m, v in sorted(d.items())))
    else:
        print(f"  {source_name:<12} (none)")
