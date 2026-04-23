import json
from google.cloud import bigquery
from google.oauth2 import service_account
from google.api_core.exceptions import Forbidden, NotFound

SA = "c:/Users/陈晨/Desktop/工单相关/newgongdan/cloudcost/xmagnet-c0e170e58dc3.json"
creds = service_account.Credentials.from_service_account_info(
    json.load(open(SA)), scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
bq = bigquery.Client(credentials=creds, project="share-service-billing")

# 1. Get VIEW's SQL definition
fqt = "share-service-billing.dinfo_billing.standard_usage_cost_detail"
t = bq.get_table(fqt)
print("=== VIEW definition ===")
print(t.view_query or "(no view_query)")
print()

# 2. Try the underlying project directly — can we see it at all?
UNDERLYING = "schy-billing-data"
print(f"\n=== probing underlying project {UNDERLYING} ===")
try:
    bq2 = bigquery.Client(credentials=creds, project=UNDERLYING)
    ds = list(bq2.list_datasets())
    print(f"  datasets: {[d.dataset_id for d in ds]}")
except Exception as e:
    print(f"  ERROR listing datasets: {type(e).__name__}: {e}")

try:
    bq2 = bigquery.Client(credentials=creds, project=UNDERLYING)
    tbl = bq2.get_table(f"{UNDERLYING}.schy_standard_usage_cost.gcp_billing_export_v1_0196EC_36C8F3_94CDC2")
    print(f"  get_table OK: rows={tbl.num_rows}")
except Exception as e:
    print(f"  get_table: {type(e).__name__}: {e}")

# 3. Also try to list ALL datasets in project share-service-billing explicitly (in case list was cached)
print("\n=== share-service-billing datasets (fresh list) ===")
for d in bq.list_datasets("share-service-billing"):
    print(f"  - {d.dataset_id}  full={d.full_dataset_id}")
    try:
        ds_obj = bq.get_dataset(f"share-service-billing.{d.dataset_id}")
        print(f"    location={ds_obj.location}  description={ds_obj.description}")
        for t in bq.list_tables(f"share-service-billing.{d.dataset_id}"):
            print(f"    * {t.table_id}  type={t.table_type}")
    except Exception as e:
        print(f"    ERROR: {e}")
