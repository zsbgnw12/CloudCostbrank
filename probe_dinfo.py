import json
from google.cloud import bigquery
from google.oauth2 import service_account

SA = "c:/Users/陈晨/Desktop/工单相关/newgongdan/cloudcost/xmagnet-c0e170e58dc3.json"
creds = service_account.Credentials.from_service_account_info(
    json.load(open(SA)), scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
PROJECT = "share-service-billing"
DATASET = "dinfo_billing"
bq = bigquery.Client(credentials=creds, project=PROJECT)

print(f"=== {PROJECT}.{DATASET} ===\n")
tables = list(bq.list_tables(f"{PROJECT}.{DATASET}"))
print(f"tables ({len(tables)}):")
for t in tables:
    print(f"  - {t.table_id}  type={t.table_type}")

print()
for t in tables:
    fqt = f"{PROJECT}.{DATASET}.{t.table_id}"
    print(f"--- {fqt} ---")
    try:
        tbl = bq.get_table(fqt)
        print(f"  rows={tbl.num_rows}  size={tbl.num_bytes}  created={tbl.created}  modified={tbl.modified}")
        print(f"  partitioning={tbl.time_partitioning}  clustering={tbl.clustering_fields}")
        cols = [(f.name, f.field_type) for f in tbl.schema]
        print(f"  schema ({len(cols)} cols): {cols[:25]}")
        # date range
        for c in ("usage_start_time", "export_time", "_PARTITIONTIME"):
            try:
                r = list(bq.query(f"SELECT MIN({c}) mn, MAX({c}) mx FROM `{fqt}`").result())[0]
                print(f"  {c}: {r.mn} ~ {r.mx}")
                break
            except Exception:
                pass
        # distinct billing_account_ids if present
        if any(n == "billing_account_id" for n, _ in cols):
            r = list(bq.query(
                f"SELECT billing_account_id, COUNT(*) c, MIN(usage_start_time) mn, MAX(usage_start_time) mx "
                f"FROM `{fqt}` GROUP BY 1 ORDER BY c DESC"
            ).result())
            print(f"  billing_accounts:")
            for row in r:
                print(f"    {row.billing_account_id}  rows={row.c}  {row.mn} ~ {row.mx}")
        # sample
        r = list(bq.query(f"SELECT * FROM `{fqt}` LIMIT 2").result())
        print(f"  sample:")
        for row in r:
            d = dict(row.items())
            # trim huge fields
            short = {k: (str(v)[:80] + "…") if len(str(v)) > 80 else v for k, v in d.items()}
            print(f"    {short}")
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {e}")
    print()
