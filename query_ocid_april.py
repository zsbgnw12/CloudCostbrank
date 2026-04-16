"""Query BQ cost for project ocid-20260212 in April 2026 (1-15)."""
import json
from google.cloud import bigquery
from google.oauth2 import service_account

SA_FILE = "c:/Users/陈晨/Desktop/工单相关/newgongdan/cloudcost/xmagnet-c0e170e58dc3.json"
PROJECT_FILTER = "ocid-20260212"
START = "2026-04-01"
END = "2026-04-15"

with open(SA_FILE) as f:
    sa_json = json.load(f)
credentials = service_account.Credentials.from_service_account_info(
    sa_json, scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
client = bigquery.Client(credentials=credentials, project=credentials.project_id)
print(f"Client project: {client.project}")

# Known data sources from the codebase
SOURCES = [
    {"name": "xmind",      "fqt": "share-service-nonprod.xmind.billing_report", "cost": "cost_at_list"},
    {"name": "cb_export",  "fqt": "cb-export.other.xm",                         "cost": "cost"},
    {"name": "px_billing", "fqt": "px-billing-report.other.xm",                 "cost": "cost"},
]

grand_total = 0.0
for s in SOURCES:
    print(f"\n=== {s['name']}: {s['fqt']} ===")
    try:
        # first: does the project even show up?
        q_total = f"""
        SELECT
          project.id AS project_id,
          SUM({s['cost']}) AS total_cost,
          COUNT(*) AS row_count
        FROM `{s['fqt']}`
        WHERE DATE(usage_start_time) BETWEEN @start AND @end
          AND project.id = @pid
        GROUP BY project.id
        """
        job = client.query(q_total, job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("start", "DATE", START),
                bigquery.ScalarQueryParameter("end",   "DATE", END),
                bigquery.ScalarQueryParameter("pid",   "STRING", PROJECT_FILTER),
            ]
        ))
        rows = list(job.result())
        if not rows:
            print(f"  No rows for project={PROJECT_FILTER} in {START}..{END}")
            continue
        for r in rows:
            print(f"  project_id={r.project_id}  total_cost={r.total_cost:.4f}  rows={r.row_count}")
            grand_total += float(r.total_cost or 0)

        # daily breakdown
        q_daily = f"""
        SELECT DATE(usage_start_time) AS d, SUM({s['cost']}) AS c
        FROM `{s['fqt']}`
        WHERE DATE(usage_start_time) BETWEEN @start AND @end
          AND project.id = @pid
        GROUP BY d ORDER BY d
        """
        job = client.query(q_daily, job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("start", "DATE", START),
                bigquery.ScalarQueryParameter("end",   "DATE", END),
                bigquery.ScalarQueryParameter("pid",   "STRING", PROJECT_FILTER),
            ]
        ))
        print("  Daily:")
        for r in job.result():
            print(f"    {r.d}  {r.c:.4f}")
    except Exception as e:
        print(f"  ERROR: {e}")

print(f"\n=== GRAND TOTAL across all sources: {grand_total:.4f} ===")
