"""Search all billing tables for any project_id matching 'daolo*' over wide date range."""
import json
from google.cloud import bigquery
from google.oauth2 import service_account

SA_PATH = "C:/Users/陈晨/Desktop/工单相关/newgongdan/cloudcost/xmagnet-c0e170e58dc3.json"

TABLES = [
    "share-service-nonprod.xmind.billing_report",
    "share-service-nonprod.testmanger.billing_report",
    "cb-export.other.xm",
    "px-billing-report.other.xm",
    "xmagnet.spaceone_billing_data_us.gcp_billing_export_v1_01186D_EC0E18_F83B2B",
]

creds = service_account.Credentials.from_service_account_info(
    json.load(open(SA_PATH)),
    scopes=["https://www.googleapis.com/auth/cloud-platform"],
)
client = bigquery.Client(credentials=creds, project=creds.project_id)

# 也列一下 SA 能看到的数据集
print("== Datasets accessible ==")
for proj in ["share-service-nonprod", "cb-export", "px-billing-report", "xmagnet"]:
    try:
        for ds in client.list_datasets(project=proj):
            print(f"  {proj}.{ds.dataset_id}")
    except Exception as e:
        print(f"  {proj}: {e}")

print("\n== Search for 'daolo*' projects across known tables (last 90d) ==")
for tbl in TABLES:
    try:
        q = f"""
          SELECT project.id AS pid, COUNT(*) AS n,
                 MIN(DATE(usage_start_time)) AS d_min,
                 MAX(DATE(usage_start_time)) AS d_max
          FROM `{tbl}`
          WHERE LOWER(project.id) LIKE 'daolo%'
            AND DATE(usage_start_time) >= DATE_SUB(CURRENT_DATE(), INTERVAL 120 DAY)
          GROUP BY pid
          ORDER BY pid
        """
        rs = list(client.query(q).result())
        if rs:
            print(f"  {tbl}:")
            for r in rs:
                print(f"    {r.pid:40} rows={r.n:8} {r.d_min}..{r.d_max}")
        else:
            print(f"  {tbl}: no daolo* rows")
    except Exception as e:
        print(f"  {tbl}: ERROR {e}")
