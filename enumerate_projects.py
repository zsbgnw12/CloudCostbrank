"""Enumerate every distinct project.id visible in each GCP data source, plus
the physical export tables, then cross-reference to find overlaps.

Read-only. Uses xmagnet as billing project (same as production code)."""
import json
from collections import defaultdict
from google.cloud import bigquery
from google.oauth2 import service_account

SA = "c:/Users/陈晨/Desktop/工单相关/newgongdan/cloudcost/xmagnet-c0e170e58dc3.json"
creds = service_account.Credentials.from_service_account_info(
    json.load(open(SA)), scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
client = bigquery.Client(credentials=creds, project=creds.project_id)

SOURCES = [
    # Configured VIEW-based data sources (ds#3-6)
    {"label":"ds#3  xmind.billing_report","fqt":"share-service-nonprod.xmind.billing_report","cost":"cost_at_list"},
    {"label":"ds#4  testmanger.billing_report","fqt":"share-service-nonprod.testmanger.billing_report","cost":"cost_at_list"},
    {"label":"ds#5  cb-export.other.xm","fqt":"cb-export.other.xm","cost":"cost"},
    {"label":"ds#6  px-billing-report.other.xm","fqt":"px-billing-report.other.xm","cost":"cost"},
    # Native exports (ds#7 = first of these)
    {"label":"ds#7  xmagnet…gcp_billing_export_v1_01186D","fqt":"xmagnet.spaceone_billing_data_us.gcp_billing_export_v1_01186D_EC0E18_F83B2B","cost":"cost_at_list"},
    # Tables not configured in DB — checking for orphan data
    {"label":"ORPHAN  xmagnet…gcp_billing_export_v1_01596E","fqt":"xmagnet.spaceone_billing_data_us.gcp_billing_export_v1_01596E_FEB6BD_B5A737","cost":"cost_at_list"},
    {"label":"ORPHAN  xmagnet…cb_export_xm_table","fqt":"xmagnet.spaceone_billing_data_us.cb_export_xm_table","cost":"cost_at_list"},
    {"label":"ORPHAN  xmagnet…px_billing_report_xm_table","fqt":"xmagnet.spaceone_billing_data_us.px_billing_report_xm_table","cost":"cost_at_list"},
    {"label":"ORPHAN  xmagnet.xmblilb.gcp_billing_export_v1_01186D","fqt":"xmagnet.xmblilb.gcp_billing_export_v1_01186D_EC0E18_F83B2B","cost":"cost_at_list"},
]

START, END = "2026-01-01", "2026-04-22"

proj_to_sources = defaultdict(list)   # project.id -> [(label, rows, cost)]
source_stats = {}                      # label -> (total_rows, total_cost, project_count, billing_account_id)

for s in SOURCES:
    q = f"""
    SELECT
      project.id AS pid,
      ANY_VALUE(billing_account_id) AS ba,
      COUNT(*) AS n,
      SUM({s['cost']}) AS c
    FROM `{s['fqt']}`
    WHERE DATE(usage_start_time) BETWEEN '{START}' AND '{END}'
    GROUP BY pid
    ORDER BY c DESC NULLS LAST
    """
    try:
        rs = list(client.query(q).result())
        total_rows = sum(r.n for r in rs)
        total_cost = sum((r.c or 0) for r in rs)
        ba_set = {r.ba for r in rs if r.ba}
        source_stats[s["label"]] = {
            "rows": total_rows, "cost": total_cost,
            "n_projects": len(rs), "billing_accounts": ba_set,
            "projects": [(r.pid, r.n, float(r.c or 0), r.ba) for r in rs],
        }
        for r in rs:
            proj_to_sources[r.pid].append((s["label"], r.n, float(r.c or 0)))
    except Exception as e:
        source_stats[s["label"]] = {"error": f"{type(e).__name__}: {str(e)[:160]}"}

# --- Report 1: per-source summary ---
print(f"=== Per-source summary ({START} ~ {END}) ===\n")
print(f"{'source':<62} {'rows':>10} {'cost $':>14} {'#proj':>6} billing_accounts")
for label, st in source_stats.items():
    if "error" in st:
        print(f"{label:<62} ERROR: {st['error']}")
        continue
    bas = ",".join(sorted(st["billing_accounts"])) or "-"
    print(f"{label:<62} {st['rows']:>10,} {st['cost']:>14,.2f} {st['n_projects']:>6}  {bas}")

# --- Report 2: project.id → which sources ---
print(f"\n=== Projects that appear in MORE THAN ONE source (overlap = double-count risk) ===\n")
overlaps = {p: srcs for p, srcs in proj_to_sources.items() if len({x[0] for x in srcs}) >= 2}
if not overlaps:
    print("  (none)")
else:
    for p, srcs in sorted(overlaps.items()):
        print(f"  project={p!r}")
        for label, rows, cost in srcs:
            print(f"      {label:<62} rows={rows:>8} cost=${cost:,.2f}")

# --- Report 3: per-source project list (brief) ---
print(f"\n=== Full project.id list per source (top by cost, all projects) ===\n")
for label, st in source_stats.items():
    if "error" in st: continue
    print(f"--- {label}  ({st['n_projects']} projects, total ${st['cost']:,.2f}) ---")
    for pid, rows, cost, ba in st["projects"]:
        print(f"   {pid:<40} rows={rows:>8,} cost=${cost:>12,.2f}  ba={ba}")
    print()
