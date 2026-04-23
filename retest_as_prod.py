"""Re-test ds#3-6 using EXACTLY the production code path:
- billing project = SA's own project (xmagnet), not the dataset's project
- SQL same shape as GCPCollector.collect_billing
"""
import json
from google.cloud import bigquery
from google.oauth2 import service_account

SA = "c:/Users/陈晨/Desktop/工单相关/newgongdan/cloudcost/xmagnet-c0e170e58dc3.json"
sa_json = json.load(open(SA))
creds = service_account.Credentials.from_service_account_info(
    sa_json, scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
# SAME AS PRODUCTION: project=credentials.project_id
client = bigquery.Client(credentials=creds, project=creds.project_id)
print(f"billing project = {client.project}\n")

SOURCES = [
    {"ds":3,"project_id":"share-service-nonprod","dataset":"xmind",     "table":"billing_report","cost_field":"cost_at_list","usage_field":"amount_in_pricing_unit"},
    {"ds":4,"project_id":"share-service-nonprod","dataset":"testmanger","table":"billing_report","cost_field":"cost_at_list","usage_field":"amount_in_pricing_unit"},
    {"ds":5,"project_id":"cb-export",            "dataset":"other",     "table":"xm",            "cost_field":"cost",        "usage_field":"amount_in_pricing_units"},
    {"ds":6,"project_id":"px-billing-report",    "dataset":"other",     "table":"xm",            "cost_field":"cost",        "usage_field":"amount_in_pricing_units"},
]

START, END = "2026-04-01", "2026-04-22"

for s in SOURCES:
    fqt = f"{s['project_id']}.{s['dataset']}.{s['table']}"
    print(f"=== ds#{s['ds']}  {fqt} ===")
    q = f"""
    SELECT
        DATE(usage_start_time) as billed_date,
        project.id as project_id,
        SUM({s['cost_field']}) as cost,
        SUM(usage.{s['usage_field']}) as usage_quantity,
        COUNT(*) as cnt
    FROM `{fqt}`
    WHERE usage_start_time >= TIMESTAMP(@s)
      AND usage_start_time < TIMESTAMP(@e) + INTERVAL 1 DAY
    GROUP BY billed_date, project_id
    ORDER BY billed_date, project_id
    """
    try:
        cfg = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("s","STRING",START),
            bigquery.ScalarQueryParameter("e","STRING",END),
        ])
        rs = list(client.query(q, job_config=cfg).result())
        print(f"  rows returned: {len(rs)}")
        for r in rs[:10]:
            print(f"    {r.billed_date} | {r.project_id} | cost={r.cost} usage={r.usage_quantity} n={r.cnt}")
        if len(rs) > 10:
            print(f"    ... {len(rs)-10} more")
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {str(e)[:300]}")
    print()
