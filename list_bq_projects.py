"""List every GCP project / BQ dataset / table visible to the xmagnet SA,
then cross-check against the data_sources configured in the app DB."""
import json, sys
from google.cloud import bigquery, resourcemanager_v3
from google.oauth2 import service_account
from google.api_core.exceptions import GoogleAPIError, Forbidden, NotFound

SA_FILE = "c:/Users/陈晨/Desktop/工单相关/newgongdan/cloudcost/xmagnet-c0e170e58dc3.json"

with open(SA_FILE) as f:
    sa_json = json.load(f)

creds = service_account.Credentials.from_service_account_info(
    sa_json, scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
print(f"[SA] email={sa_json['client_email']}  home_project={sa_json['project_id']}\n")

# --- 1. Projects the SA can see via Resource Manager ---
print("=" * 70)
print("STEP 1 — projects visible to this SA (resource manager search)")
print("=" * 70)
visible_projects = []
try:
    rm = resourcemanager_v3.ProjectsClient(credentials=creds)
    for p in rm.search_projects(request={"query": ""}):
        visible_projects.append(p.project_id)
        print(f"  - {p.project_id}   (name={p.display_name}, state={p.state.name})")
except Exception as e:
    print(f"  resource manager search failed: {e}")

# --- 2. Configured project_ids from DB (from earlier query) ---
DB_SOURCES = [
    {"ds": 3, "name": "GCP-xmind",      "project_id": "share-service-nonprod", "dataset": "xmind",                       "table": "billing_report"},
    {"ds": 4, "name": "GCP-testmanger", "project_id": "share-service-nonprod", "dataset": "testmanger",                  "table": "billing_report"},
    {"ds": 5, "name": "GCP-cb_export",  "project_id": "cb-export",             "dataset": "other",                       "table": "xm"},
    {"ds": 6, "name": "GCP-px_billing", "project_id": "px-billing-report",     "dataset": "other",                       "table": "xm"},
    {"ds": 7, "name": "GCP-us_native",  "project_id": "xmagnet",               "dataset": "spaceone_billing_data_us",    "table": "gcp_billing_export_v1_01186D_EC0E18_F83B2B"},
]

# --- 3. For each configured + each visible project, list BQ datasets ---
print("\n" + "=" * 70)
print("STEP 2 — BQ datasets per project (probe each configured and each visible)")
print("=" * 70)

probe_projects = sorted(set(visible_projects) | {s["project_id"] for s in DB_SOURCES})
dataset_map = {}  # project -> [dataset ids]
for proj in probe_projects:
    try:
        bq = bigquery.Client(credentials=creds, project=proj)
        ds_list = [d.dataset_id for d in bq.list_datasets(project=proj)]
        dataset_map[proj] = ds_list
        print(f"  [{proj}]  datasets={ds_list}")
    except Forbidden as e:
        dataset_map[proj] = None
        print(f"  [{proj}]  FORBIDDEN: {e.message}")
    except NotFound as e:
        dataset_map[proj] = "NOT_FOUND"
        print(f"  [{proj}]  PROJECT NOT FOUND")
    except Exception as e:
        dataset_map[proj] = None
        print(f"  [{proj}]  ERROR: {type(e).__name__}: {e}")

# --- 4. Verify each DB data_source's table actually exists + row/date range ---
print("\n" + "=" * 70)
print("STEP 3 — verify each DB data_source points at a real BQ table")
print("=" * 70)
for s in DB_SOURCES:
    fqt = f"{s['project_id']}.{s['dataset']}.{s['table']}"
    print(f"\n  ds#{s['ds']} {s['name']}  ->  {fqt}")
    try:
        bq = bigquery.Client(credentials=creds, project=s["project_id"])
        t = bq.get_table(fqt)
        print(f"    OK: rows={t.num_rows}, created={t.created}")
        # Try min/max export_time or usage_start_time
        for col in ("usage_start_time", "export_time", "_PARTITIONTIME"):
            try:
                r = list(bq.query(f"SELECT MIN({col}) mn, MAX({col}) mx FROM `{fqt}`").result())[0]
                print(f"    {col}: {r.mn} ~ {r.mx}")
                break
            except Exception:
                continue
    except Forbidden as e:
        print(f"    FORBIDDEN: {e.message}")
    except NotFound as e:
        print(f"    NOT_FOUND: {e.message}")
    except Exception as e:
        print(f"    ERROR: {type(e).__name__}: {e}")

# --- 5. Highlight mismatches ---
print("\n" + "=" * 70)
print("STEP 4 — DB project_id NOT in SA-visible project list")
print("=" * 70)
visible_set = set(visible_projects)
for s in DB_SOURCES:
    if s["project_id"] not in visible_set:
        print(f"  ds#{s['ds']} {s['name']}: project_id={s['project_id']!r} NOT visible to SA")
    else:
        print(f"  ds#{s['ds']} {s['name']}: project_id={s['project_id']!r} OK")
