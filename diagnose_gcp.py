"""Diagnose why cb_export and px_billing returned 0 rows."""
import sys, json
sys.path.insert(0, "c:/Users/陈晨/Desktop/工单相关/newgongdan/cloudcost")

from google.cloud import bigquery
from google.oauth2 import service_account

SA_FILE = "c:/Users/陈晨/Desktop/工单相关/newgongdan/cloudcost/xmagnet-c0e170e58dc3.json"

with open(SA_FILE) as f:
    sa_json = json.load(f)

credentials = service_account.Credentials.from_service_account_info(
    sa_json, scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
client = bigquery.Client(credentials=credentials, project=credentials.project_id)
print(f"Client project: {client.project}\n")

SOURCES = [
    {"name": "cb_export",  "project_id": "cb-export",         "dataset": "other", "table": "xm"},
    {"name": "px_billing", "project_id": "px-billing-report",  "dataset": "other", "table": "xm"},
]

for s in SOURCES:
    fqt = f"{s['project_id']}.{s['dataset']}.{s['table']}"
    print(f"=== {s['name']}: `{fqt}` ===")

    # 1. Check table schema
    try:
        t = client.get_table(fqt)
        print(f"  Table found. Rows: {t.num_rows}")
        cols = [f.name for f in t.schema]
        print(f"  Columns ({len(cols)}): {cols[:20]}")
        # Check for date column
        date_cols = [c for c in cols if "time" in c.lower() or "date" in c.lower()]
        print(f"  Date-like columns: {date_cols}")
    except Exception as e:
        print(f"  ERROR getting table: {e}")
        continue

    # 2. Count rows with no filter
    try:
        r = client.query(f"SELECT COUNT(*) as cnt FROM `{fqt}`")
        cnt = list(r.result())[0].cnt
        print(f"  Total rows (no filter): {cnt}")
    except Exception as e:
        print(f"  ERROR counting: {e}")

    # 3. Find min/max date
    try:
        r = client.query(f"SELECT MIN(usage_start_time), MAX(usage_start_time) FROM `{fqt}`")
        row = list(r.result())[0]
        print(f"  Date range: {row[0]} ~ {row[1]}")
    except Exception as e:
        print(f"  No usage_start_time or error: {e}")
        # Try other date columns
        try:
            r = client.query(f"SELECT MIN(export_time), MAX(export_time) FROM `{fqt}`")
            row = list(r.result())[0]
            print(f"  export_time range: {row[0]} ~ {row[1]}")
        except Exception as e2:
            print(f"  Also no export_time: {e2}")

    # 4. Peek first 3 rows
    try:
        r = client.query(f"SELECT * FROM `{fqt}` LIMIT 3")
        rows = list(r.result())
        print(f"  Sample rows ({len(rows)}):")
        for row in rows:
            print(f"    {dict(row.items())}")
    except Exception as e:
        print(f"  ERROR sampling: {e}")

    print()
