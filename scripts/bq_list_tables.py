"""List all tables in accessible datasets, then search for daolo* in them."""
import json
from google.cloud import bigquery
from google.oauth2 import service_account

SA_PATH = "C:/Users/陈晨/Desktop/工单相关/newgongdan/cloudcost/xmagnet-c0e170e58dc3.json"
creds = service_account.Credentials.from_service_account_info(
    json.load(open(SA_PATH)), scopes=["https://www.googleapis.com/auth/cloud-platform"],
)
client = bigquery.Client(credentials=creds, project=creds.project_id)

# 列 datasets 下的全部 tables
DATASETS = [
    "share-service-nonprod.testmanger",
    "share-service-nonprod.xmind",
    "xmagnet.spaceone_billing_data_us",
    "xmagnet.vm",
    "xmagnet.xmblilb",
]

candidate_tables = []
for ds in DATASETS:
    proj, dset = ds.split(".", 1)
    try:
        for t in client.list_tables(f"{proj}.{dset}"):
            full = f"{proj}.{dset}.{t.table_id}"
            print(f"{full}\t{t.table_type}")
            # 只搜账单类表
            if "billing" in t.table_id.lower() or t.table_id in ("xm",):
                candidate_tables.append(full)
    except Exception as e:
        print(f"{ds} ERROR: {e}")

print(f"\n== candidates for daolo* search: {len(candidate_tables)} ==")
for tbl in candidate_tables:
    try:
        q = f"""
          SELECT project.id AS pid, COUNT(*) AS n
          FROM `{tbl}`
          WHERE LOWER(project.id) LIKE 'daolo%'
            AND DATE(usage_start_time) >= DATE_SUB(CURRENT_DATE(), INTERVAL 180 DAY)
          GROUP BY pid
        """
        rs = list(client.query(q).result())
        if rs:
            print(f"\n{tbl}:")
            for r in rs:
                print(f"  {r.pid:40} rows={r.n}")
    except Exception as e:
        print(f"{tbl}: {e}")
