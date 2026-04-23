"""Verify Issue #1: which projects under billing_account 01186D-EC0E18-F83B2B are
NOT covered by any of ds#3-6 VIEWs, i.e. completely dropped after ds#7 was disabled.

Method:
  A = set of project.id in 01186D native export (ds#7 table)
  B = union of project.id in ds#3 + ds#4 + ds#5 + ds#6 VIEWs
  Gap = A - B   (these projects' cost is completely lost)
"""
import json
from google.cloud import bigquery
from google.oauth2 import service_account

SA = "c:/Users/陈晨/Desktop/工单相关/newgongdan/cloudcost/xmagnet-c0e170e58dc3.json"
creds = service_account.Credentials.from_service_account_info(
    json.load(open(SA)), scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
client = bigquery.Client(credentials=creds, project=creds.project_id)

START, END = "2026-01-01", "2026-04-22"

def distinct_projects(fqt, cost_field="cost_at_list"):
    q = f"""
    SELECT project.id AS pid, COUNT(*) n, SUM({cost_field}) c, MIN(DATE(usage_start_time)) mn, MAX(DATE(usage_start_time)) mx
    FROM `{fqt}`
    WHERE DATE(usage_start_time) BETWEEN '{START}' AND '{END}'
      AND project.id IS NOT NULL
    GROUP BY pid
    """
    return {r.pid: {"n": r.n, "c": float(r.c or 0), "mn": r.mn, "mx": r.mx}
            for r in client.query(q).result()}

# A: projects in 01186D native export
print("Querying ds#7 (01186D native)...")
A_7 = distinct_projects("xmagnet.spaceone_billing_data_us.gcp_billing_export_v1_01186D_EC0E18_F83B2B")
print(f"  {len(A_7)} projects, total cost=${sum(p['c'] for p in A_7.values()):,.2f}")

# Also use the newer-data source xmblilb copy (has data through 4-22 vs ds#7 stopping at 3-23)
print("Querying xmblilb copy (same BA, newer data)...")
A_xmblilb = distinct_projects("xmagnet.xmblilb.gcp_billing_export_v1_01186D_EC0E18_F83B2B")
print(f"  {len(A_xmblilb)} projects, total cost=${sum(p['c'] for p in A_xmblilb.values()):,.2f}")

# Union all projects from ds#3-6 VIEWs
print("Querying ds#3-6 VIEW projects...")
B = {}
for label, fqt, cf in [
    ("ds#3", "share-service-nonprod.xmind.billing_report",     "cost_at_list"),
    ("ds#4", "share-service-nonprod.testmanger.billing_report","cost_at_list"),
    ("ds#5", "cb-export.other.xm",                             "cost"),
    ("ds#6", "px-billing-report.other.xm",                     "cost"),
]:
    p = distinct_projects(fqt, cf)
    print(f"  {label}: {len(p)} projects")
    for pid in p:
        B.setdefault(pid, []).append(label)

# Union A = ds#7 ∪ xmblilb (= full 01186D data)
A = {**A_7}
for pid, v in A_xmblilb.items():
    if pid in A:
        # keep whichever has longer window
        if v["mx"] > A[pid]["mx"]:
            A[pid] = v
    else:
        A[pid] = v

gap = {pid: info for pid, info in A.items() if pid not in B}
covered = {pid: info for pid, info in A.items() if pid in B}

print(f"\n=== Summary ===")
print(f"01186D-BA projects total:              {len(A)}  cost=${sum(p['c'] for p in A.values()):,.2f}")
print(f"  covered by at least one VIEW (ds3-6): {len(covered)}  cost=${sum(p['c'] for p in covered.values()):,.2f}")
print(f"  GAP (not in any VIEW, DROPPED):       {len(gap)}  cost=${sum(p['c'] for p in gap.values()):,.2f}")

print(f"\n=== Projects in the GAP (dropped because ds#7 is off) ===")
for pid, info in sorted(gap.items(), key=lambda kv: -kv[1]["c"]):
    print(f"  {pid:<35} rows={info['n']:>7,}  cost=${info['c']:>11,.2f}  {info['mn']} ~ {info['mx']}")

print(f"\n=== (For reference) Projects from 01186D that ARE covered ===")
for pid, info in sorted(covered.items(), key=lambda kv: -kv[1]["c"])[:20]:
    via = ",".join(B[pid])
    print(f"  {pid:<35} via {via:<25} cost=${info['c']:>11,.2f}")
