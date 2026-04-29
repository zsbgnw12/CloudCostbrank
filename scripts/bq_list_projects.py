"""List all distinct project_id from BQ billing tables, last 90d."""
import json
from google.cloud import bigquery
from google.oauth2 import service_account

SA_PATH = "C:/Users/陈晨/Desktop/工单相关/newgongdan/cloudcost/xmagnet-c0e170e58dc3.json"
creds = service_account.Credentials.from_service_account_info(
    json.load(open(SA_PATH)), scopes=["https://www.googleapis.com/auth/cloud-platform"],
)
client = bigquery.Client(credentials=creds, project=creds.project_id)

TABLES = [
    "share-service-nonprod.xmind.billing_report",
    "share-service-nonprod.testmanger.billing_report",
    "cb-export.other.xm",
    "px-billing-report.other.xm",
    "xmagnet.spaceone_billing_data_us.gcp_billing_export_v1_01186D_EC0E18_F83B2B",
]

all_pids = set()
for tbl in TABLES:
    try:
        q = f"""
          SELECT DISTINCT project.id AS pid
          FROM `{tbl}`
          WHERE DATE(usage_start_time) >= DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY)
            AND project.id IS NOT NULL
        """
        rs = list(client.query(q).result())
        ids = sorted({r.pid for r in rs if r.pid})
        all_pids.update(ids)
        print(f"\n{tbl}: {len(ids)} projects")
        for p in ids:
            print(f"  {p}")
    except Exception as e:
        print(f"{tbl}: ERROR {type(e).__name__}: {str(e)[:150]}")
