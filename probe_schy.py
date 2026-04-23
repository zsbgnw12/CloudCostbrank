import json
from google.cloud import bigquery
from google.oauth2 import service_account

SA = "c:/Users/陈晨/Desktop/工单相关/newgongdan/cloudcost/xmagnet-c0e170e58dc3.json"
creds = service_account.Credentials.from_service_account_info(
    json.load(open(SA)), scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
bq = bigquery.Client(credentials=creds, project="schy-billing-data")

for ds_id in ["dinfo_billing", "schy_detailed_usage_cost"]:
    print(f"\n=== schy-billing-data.{ds_id} ===")
    try:
        for t in bq.list_tables(f"schy-billing-data.{ds_id}"):
            fqt = f"schy-billing-data.{ds_id}.{t.table_id}"
            try:
                tbl = bq.get_table(fqt)
                print(f"  - {t.table_id}  type={t.table_type}  rows={tbl.num_rows}")
            except Exception as e:
                print(f"  - {t.table_id}  type={t.table_type}  (get_table err: {type(e).__name__})")
            if t.table_type == "VIEW":
                try:
                    tbl = bq.get_table(fqt)
                    q = (tbl.view_query or "")[:300]
                    print(f"      view_query: {q}")
                except Exception:
                    pass
    except Exception as e:
        print(f"  list_tables ERROR: {type(e).__name__}: {e}")
