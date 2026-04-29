"""Final search: only proper-schema tables."""
import json
from google.cloud import bigquery
from google.oauth2 import service_account

SA_PATH = "C:/Users/陈晨/Desktop/工单相关/newgongdan/cloudcost/xmagnet-c0e170e58dc3.json"
creds = service_account.Credentials.from_service_account_info(
    json.load(open(SA_PATH)), scopes=["https://www.googleapis.com/auth/cloud-platform"],
)
client = bigquery.Client(credentials=creds, project=creds.project_id)

# 只查 usage_start_time 是 TIMESTAMP 类型的标准表
TABLES = [
    "share-service-nonprod.xmind.billing_report",
    "share-service-nonprod.testmanger.billing_report",
    "xmagnet.spaceone_billing_data_us.gcp_billing_export_v1_01186D_EC0E18_F83B2B",
    "xmagnet.spaceone_billing_data_us.gcp_billing_export_resource_v1_01186D_EC0E18_F83B2B",
    "xmagnet.xmblilb.gcp_billing_export_v1_01186D_EC0E18_F83B2B",
    "xmagnet.xmblilb.gcp_billing_export_resource_v1_01186D_EC0E18_F83B2B",
]

for tbl in TABLES:
    try:
        q = f"""
          SELECT project.id AS pid, COUNT(*) AS n,
                 MIN(DATE(usage_start_time)) AS d_min,
                 MAX(DATE(usage_start_time)) AS d_max
          FROM `{tbl}`
          WHERE LOWER(project.id) LIKE 'daolo%'
            AND DATE(usage_start_time) >= DATE_SUB(CURRENT_DATE(), INTERVAL 180 DAY)
          GROUP BY pid
          ORDER BY pid
        """
        rs = list(client.query(q).result())
        if rs:
            print(f"\n{tbl}:")
            for r in rs:
                print(f"  {r.pid}  rows={r.n}  {r.d_min}~{r.d_max}")
        else:
            print(f"{tbl}: no daolo* rows in 180d")
    except Exception as e:
        print(f"{tbl}: ERROR {type(e).__name__}: {str(e)[:100]}")
