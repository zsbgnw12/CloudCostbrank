"""核对：BQ 原表 px-billing-report.other.xm 的全部字段 vs 我们 CSV 输出的字段。"""
import json
from google.cloud import bigquery
from google.oauth2 import service_account

SA_PATH = "C:/Users/陈晨/Desktop/工单相关/newgongdan/cloudcost/xmagnet-c0e170e58dc3.json"
TABLE = "px-billing-report.other.xm"

creds = service_account.Credentials.from_service_account_info(
    json.load(open(SA_PATH)), scopes=["https://www.googleapis.com/auth/cloud-platform"],
)
client = bigquery.Client(credentials=creds, project=creds.project_id)

# 列出 BQ 表所有字段（含 STRUCT 子字段）
def walk(field, prefix=""):
    name = f"{prefix}{field.name}"
    if field.field_type in ("RECORD", "STRUCT"):
        for sub in field.fields:
            yield from walk(sub, prefix=f"{name}.")
    else:
        yield (name, field.field_type, field.mode)

tbl = client.get_table(TABLE)
all_paths = list()
for f in tbl.schema:
    for p in walk(f):
        all_paths.append(p)

print(f"=== BQ 原表 `{TABLE}` 全部字段（含 STRUCT 子字段）===\n")
for path, ftype, mode in all_paths:
    print(f"  {path}\t{ftype}\t{mode}")

# 我们 CSV 里输出的字段（英文名 → BQ 原 path 映射）
csv_export = [
    ("billing_account_id",                 "billing_account_id"),
    ("service_id",                         "service.id"),
    ("service_description",                "service.description"),
    ("sku_id",                             "sku.id"),
    ("sku_description",                    "sku.description"),
    ("usage_start_time",                   "usage_start_time"),
    ("usage_end_time",                     "usage_end_time"),
    ("project_id",                         "project.id"),
    ("project_name",                       "project.name"),
    ("project_number",                     "project.number"),
    ("project_labels",                     "project.labels"),
    ("labels",                             "labels"),
    ("location",                           "location.location"),
    ("country",                            "location.country"),
    ("region",                             "location.region"),
    ("zone",                               "location.zone"),
    ("currency",                           "currency"),
    ("currency_conversion_rate",           "currency_conversion_rate"),
    ("usage_amount",                       "usage.amount"),
    ("usage_unit_raw",                     "usage.unit"),
    ("usage_amount_in_pricing_units",      "usage.amount_in_pricing_units"),
    ("usage_pricing_unit",                 "usage.pricing_unit"),
    ("cost",                               "cost"),
    ("cost_at_list",                       "cost_at_list"),
    ("cost_type",                          "cost_type"),
    ("credits",                            "credits"),
    ("adjustment_info",                    "adjustment_info"),
    ("invoice_month",                      "invoice.month"),
    ("transaction_type",                   "transaction_type"),
    ("seller_name",                        "seller_name"),
    ("consumption_model_id",               "consumption_model.id"),
    ("consumption_model_description",      "consumption_model.description"),
    ("resource_name",                      "resource.name"),
    ("resource_global_name",               "resource.global_name"),
    ("system_labels",                      "system_labels"),
    ("export_time",                        "export_time"),
]

bq_paths_set = {p for p, _, _ in all_paths}
# adjustment_info / consumption_model / project.labels / labels / system_labels / credits 这些是 RECORD/REPEATED
# 取 leaf 节点用顶层路径前缀匹配
bq_top_records = set()
for p, ftype, mode in all_paths:
    if "." in p:
        bq_top_records.add(p.split(".", 1)[0])

print("\n=== CSV 字段 vs BQ 字段核对 ===\n")
print(f"{'CSV 列名':<35} {'映射到 BQ 路径':<45} {'BQ 是否存在'}")
print("-" * 100)
missing = []
for csv_col, bq_path in csv_export:
    # 直接命中
    if bq_path in bq_paths_set:
        status = "OK (leaf)"
    # 顶层 RECORD（如 credits/labels/adjustment_info/system_labels）整存
    elif bq_path in bq_top_records or any(p == bq_path for p, _, _ in all_paths if p.startswith(bq_path)):
        # 可能是 RECORD/REPEATED，TO_JSON_STRING 整体导
        if any(p.startswith(bq_path + ".") or p == bq_path for p, _, _ in all_paths):
            status = "OK (record TO_JSON_STRING)"
        else:
            status = "MISSING"
            missing.append(csv_col)
    else:
        status = "MISSING (BQ 表无此字段)"
        missing.append(csv_col)
    print(f"  {csv_col:<33} {bq_path:<43} {status}")

print()
print(f"=== BQ 表里有、但 CSV 没导出的字段 ===\n")
csv_bq_paths = {bq_path for _, bq_path in csv_export}
csv_bq_top = {p.split(".", 1)[0] for p in csv_bq_paths}
for path, ftype, mode in all_paths:
    top = path.split(".", 1)[0]
    if path not in csv_bq_paths and top not in csv_bq_top:
        print(f"  {path}\t{ftype}\t{mode}")
print()
if missing:
    print(f"!! CSV 列在 BQ 表中找不到对应字段：{missing}")
else:
    print("✓ CSV 所有列都能映射到 BQ 字段")
