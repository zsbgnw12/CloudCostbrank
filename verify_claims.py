"""Verify each claimed 'problem' against actual BQ data.
Shoots down false alarms, confirms real ones. Read-only."""
import json
from google.cloud import bigquery
from google.oauth2 import service_account

SA = "c:/Users/陈晨/Desktop/工单相关/newgongdan/cloudcost/xmagnet-c0e170e58dc3.json"
creds = service_account.Credentials.from_service_account_info(
    json.load(open(SA)), scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
client = bigquery.Client(credentials=creds, project=creds.project_id)

VIEWS = [
    ("ds#3", "share-service-nonprod.xmind.billing_report"),
    ("ds#4", "share-service-nonprod.testmanger.billing_report"),
    ("ds#5", "cb-export.other.xm"),
    ("ds#6", "px-billing-report.other.xm"),
]

print("=" * 70)
print("CLAIM 1: Cross-currency SUM — is there non-USD data in GCP VIEWs?")
print("=" * 70)
for label, fqt in VIEWS:
    try:
        r = list(client.query(f"""
            SELECT currency, COUNT(*) n, SUM(cost_at_list) c
            FROM `{fqt}`
            WHERE DATE(usage_start_time) BETWEEN '2026-01-01' AND '2026-04-22'
            GROUP BY currency
        """).result())
        print(f"  {label}:")
        for row in r:
            print(f"    currency={row.currency!r:<10} rows={row.n:>9,}  cost={row.c:>14,.2f}")
    except Exception as e:
        print(f"  {label}: error {str(e)[:100]}")

print()
print("=" * 70)
print("CLAIM 2: region NULL — does GCP collector SQL actually produce NULL region?")
print("=" * 70)
# Mimic gcp_collector.py's SQL: IFNULL(location.region, 'global')
for label, fqt in VIEWS[:2]:  # only check xmind/testmanger (fastest)
    try:
        r = list(client.query(f"""
            SELECT
              IFNULL(location.region, 'global') AS region_out,
              COUNT(*) n
            FROM `{fqt}`
            WHERE DATE(usage_start_time) BETWEEN '2026-04-01' AND '2026-04-22'
            GROUP BY region_out
            ORDER BY n DESC
            LIMIT 10
        """).result())
        print(f"  {label}:")
        for row in r:
            print(f"    region={row.region_out!r:<30} rows={row.n:>9,}")
    except Exception as e:
        print(f"  {label}: error {str(e)[:100]}")

print()
print("=" * 70)
print("CLAIM 9: duplicate external_project_id — are the same project.id values")
print("         appearing across DIFFERENT VIEWs? (if yes, auto-create may")
print("         insert dup Project rows under different supply_sources)")
print("=" * 70)
# From prior enumeration we know: ds#3 projects (wecut-*, deep-science-1),
# ds#4 (mafhoom-*, lyww-*), ds#5 (chuer-*, ocid-*), ds#6 (gemini-*, test-*)
# auto_create_gcp_projects puts ALL under one supply_source (ensure_other_gcp_supply_source_id_sync).
# So even if same pid appears in 2 VIEWs, Project has UNIQUE(supply_source_id, ext_id) → only 1 row.
# But if a human later manually creates another Project row under a different SupplySource for same pid → dup.
# Let's measure: count project.id values shared between VIEWs.
sql_intersect = """
WITH a3 AS (SELECT DISTINCT project.id pid FROM `share-service-nonprod.xmind.billing_report` WHERE DATE(usage_start_time) >= '2026-01-01'),
     a4 AS (SELECT DISTINCT project.id pid FROM `share-service-nonprod.testmanger.billing_report` WHERE DATE(usage_start_time) >= '2026-01-01'),
     a5 AS (SELECT DISTINCT project.id pid FROM `cb-export.other.xm` WHERE DATE(usage_start_time) >= '2026-01-01'),
     a6 AS (SELECT DISTINCT project.id pid FROM `px-billing-report.other.xm` WHERE DATE(usage_start_time) >= '2026-01-01')
SELECT
  (SELECT COUNT(*) FROM a3 INNER JOIN a4 USING (pid)) a3_a4,
  (SELECT COUNT(*) FROM a3 INNER JOIN a5 USING (pid)) a3_a5,
  (SELECT COUNT(*) FROM a3 INNER JOIN a6 USING (pid)) a3_a6,
  (SELECT COUNT(*) FROM a4 INNER JOIN a5 USING (pid)) a4_a5,
  (SELECT COUNT(*) FROM a4 INNER JOIN a6 USING (pid)) a4_a6,
  (SELECT COUNT(*) FROM a5 INNER JOIN a6 USING (pid)) a5_a6
"""
r = list(client.query(sql_intersect).result())[0]
print(f"  project.id overlap between pairs of VIEWs:")
print(f"    ds3 ∩ ds4 = {r.a3_a4}")
print(f"    ds3 ∩ ds5 = {r.a3_a5}")
print(f"    ds3 ∩ ds6 = {r.a3_a6}")
print(f"    ds4 ∩ ds5 = {r.a4_a5}")
print(f"    ds4 ∩ ds6 = {r.a4_a6}")
print(f"    ds5 ∩ ds6 = {r.a5_a6}")
