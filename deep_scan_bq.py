"""Deep scan every GCP project the SA can touch, list every dataset & table,
including the 6 projects referenced in the dinfo_billing VIEW filter."""
import json
from google.cloud import bigquery, resourcemanager_v3
from google.oauth2 import service_account
from google.api_core.exceptions import Forbidden, NotFound

SA = "c:/Users/陈晨/Desktop/工单相关/newgongdan/cloudcost/xmagnet-c0e170e58dc3.json"
creds = service_account.Credentials.from_service_account_info(
    json.load(open(SA)), scopes=["https://www.googleapis.com/auth/cloud-platform"]
)

# Every project we know about, from all sources
PROJECTS = sorted(set([
    # SA home
    "xmagnet",
    # resource-manager visible
    "share-service-billing",
    # BQ probe previously worked
    "share-service-nonprod",
    "cb-export",
    "px-billing-report",
    # VIEW's underlying project
    "schy-billing-data",
    # Six projects in the VIEW's WHERE clause
    "lykj-gemini-testl",
    "lykj-gemini-prod1",
    "lykj-gemini-prod2",
    "lykj-search-prod1",  # note trailing space in view, strip here
    "dfaistudio-tt10",
    "dfgzy-260313-1",
]))

def describe_table(bq, fqt, ttype):
    try:
        t = bq.get_table(fqt)
        info = {
            "type": ttype,
            "rows": t.num_rows,
            "bytes": t.num_bytes,
            "created": str(t.created) if t.created else None,
            "modified": str(t.modified) if t.modified else None,
            "partitioning": str(t.time_partitioning) if t.time_partitioning else None,
            "clustering": t.clustering_fields,
            "n_cols": len(t.schema),
            "col_names": [f.name for f in t.schema][:30],
        }
        if ttype == "VIEW":
            info["view_query"] = (t.view_query or "")[:500]
        # date range probe
        for c in ("usage_start_time", "export_time", "_PARTITIONTIME"):
            if any(f.name == c for f in t.schema) or c == "_PARTITIONTIME":
                try:
                    r = list(bq.query(f"SELECT MIN({c}) mn, MAX({c}) mx, COUNT(*) cnt FROM `{fqt}`").result())[0]
                    info[f"range_{c}"] = f"{r.mn} ~ {r.mx}  cnt={r.cnt}"
                    break
                except Exception as e:
                    info[f"range_{c}_err"] = f"{type(e).__name__}"
        return info
    except Forbidden as e:
        return {"type": ttype, "error": "FORBIDDEN", "detail": str(e)[:200]}
    except NotFound:
        return {"type": ttype, "error": "NOT_FOUND"}
    except Exception as e:
        return {"type": ttype, "error": f"{type(e).__name__}: {str(e)[:200]}"}

def scan_project(proj):
    print(f"\n{'='*78}\nPROJECT: {proj}\n{'='*78}")
    try:
        bq = bigquery.Client(credentials=creds, project=proj)
        datasets = list(bq.list_datasets(proj))
    except Forbidden as e:
        print(f"  FORBIDDEN (cannot list datasets): {str(e)[:200]}")
        return
    except NotFound:
        print(f"  PROJECT NOT FOUND")
        return
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {e}")
        return

    if not datasets:
        print("  (no datasets visible — project reachable but empty or no perm on any dataset)")
        return

    for ds in datasets:
        ds_full = f"{proj}.{ds.dataset_id}"
        print(f"\n  [dataset] {ds_full}")
        try:
            ds_obj = bq.get_dataset(ds_full)
            print(f"    location={ds_obj.location}  created={ds_obj.created}  description={ds_obj.description!r}")
        except Exception as e:
            print(f"    (get_dataset err: {type(e).__name__})")
        try:
            tables = list(bq.list_tables(ds_full))
        except Exception as e:
            print(f"    list_tables ERROR: {type(e).__name__}: {str(e)[:200]}")
            continue
        print(f"    tables ({len(tables)}):")
        for t in tables:
            fqt = f"{ds_full}.{t.table_id}"
            info = describe_table(bq, fqt, t.table_type)
            print(f"      - {t.table_id}  {info}")

for p in PROJECTS:
    scan_project(p)
