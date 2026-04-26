"""Pre-change impact analysis: which BQ sources support which fields."""
import json
from google.cloud import bigquery
from google.oauth2 import service_account

SA = "c:/Users/陈晨/Desktop/工单相关/newgongdan/cloudcost/xmagnet-c0e170e58dc3.json"
creds = service_account.Credentials.from_service_account_info(
    json.load(open(SA)), scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
client = bigquery.Client(credentials=creds, project=creds.project_id)

SOURCES = [
    ("ds#3 xmind VIEW", "share-service-nonprod.xmind.billing_report"),
    ("ds#4 testmanger VIEW", "share-service-nonprod.testmanger.billing_report"),
    ("ds#5 cb-export VIEW", "cb-export.other.xm"),
    ("ds#6 px-billing VIEW", "px-billing-report.other.xm"),
    ("ds#7 native 01186D", "xmagnet.spaceone_billing_data_us.gcp_billing_export_v1_01186D_EC0E18_F83B2B"),
]

WANTED = ["cost", "cost_at_list", "credits", "resource", "cost_type", "service", "sku"]

for label, fqt in SOURCES:
    try:
        t = client.get_table(fqt)
        cols = {f.name: f.field_type for f in t.schema}
        # also check nested fields
        service_subfields = []
        sku_subfields = []
        resource_subfields = []
        for f in t.schema:
            if f.name == "service" and f.field_type == "RECORD":
                service_subfields = [s.name for s in f.fields]
            if f.name == "sku" and f.field_type == "RECORD":
                sku_subfields = [s.name for s in f.fields]
            if f.name == "resource" and f.field_type == "RECORD":
                resource_subfields = [s.name for s in f.fields]
        print(f"\n=== {label} ===")
        for w in WANTED:
            if w in cols:
                extra = ""
                if w == "service": extra = f"  subfields={service_subfields}"
                if w == "sku": extra = f"  subfields={sku_subfields}"
                if w == "resource": extra = f"  subfields={resource_subfields}"
                print(f"  [OK]  {w:<14} ({cols[w]}){extra}")
            else:
                print(f"  [--]  {w:<14} (MISSING)")
    except Exception as e:
        print(f"\n=== {label} ===\n  ERROR: {e}")
