"""自查：BQ 真实 schema vs 我们 22 列。
列出 BQ 完整 schema、示例数据、和我们的字段对照、找出我答错或漏的地方。"""
import json
from google.cloud import bigquery
from google.oauth2 import service_account

SA = "c:/Users/陈晨/Desktop/工单相关/newgongdan/cloudcost/xmagnet-c0e170e58dc3.json"
creds = service_account.Credentials.from_service_account_info(
    json.load(open(SA)), scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
client = bigquery.Client(credentials=creds, project=creds.project_id)

# 选 native 01186D 作主参照（schema 最全），cb-export VIEW 作 VIEW 参照
TABLES = [
    ("native 01186D", "xmagnet.spaceone_billing_data_us.gcp_billing_export_v1_01186D_EC0E18_F83B2B"),
    ("xmind VIEW",    "share-service-nonprod.xmind.billing_report"),
    ("cb-export VIEW","cb-export.other.xm"),
]

def dump_schema(field, indent=0):
    """递归打印 BQ schema (RECORD 内嵌)。"""
    pad = "  " * indent
    if field.field_type == "RECORD":
        print(f"{pad}{field.name} : RECORD ({field.mode}) — 子字段:")
        for sub in field.fields:
            dump_schema(sub, indent + 1)
    else:
        print(f"{pad}{field.name} : {field.field_type} ({field.mode})")

print("=" * 80)
print("Section A: BQ 全字段 schema")
print("=" * 80)
for label, fqt in TABLES:
    print(f"\n--- {label}: {fqt} ---")
    try:
        t = client.get_table(fqt)
        print(f"行数 ≈ {t.num_rows:,}, partition: {t.time_partitioning}")
        for f in t.schema:
            dump_schema(f)
    except Exception as e:
        print(f"  ERROR: {e}")

print()
print("=" * 80)
print("Section B: 示例真实数据（2026-04-22, 选 1 行）")
print("=" * 80)
q = """
SELECT *
FROM `xmagnet.spaceone_billing_data_us.gcp_billing_export_v1_01186D_EC0E18_F83B2B`
WHERE DATE(usage_start_time) = '2026-03-15'
  AND cost_at_list > 1
LIMIT 1
"""
for row in client.query(q).result():
    d = dict(row.items())
    for k, v in d.items():
        s = str(v)
        if len(s) > 200: s = s[:200] + "..."
        print(f"  {k:<35} = {s}")
    break

print()
print("=" * 80)
print("Section C: credits 数组里都有哪些 type？(实证)")
print("=" * 80)
q = """
SELECT c.type type, COUNT(*) n, ROUND(SUM(c.amount), 2) total_amount
FROM `xmagnet.spaceone_billing_data_us.gcp_billing_export_v1_01186D_EC0E18_F83B2B`,
     UNNEST(credits) c
WHERE DATE(usage_start_time) >= '2025-10-01'
GROUP BY type ORDER BY n DESC
"""
for row in client.query(q).result():
    print(f"  type={row.type!r:<40} count={row.n:>9,}  amount_sum={row.total_amount}")

print()
print("=" * 80)
print("Section D: cost_type 实际取值")
print("=" * 80)
q = """
SELECT cost_type, COUNT(*) n, ROUND(SUM(cost), 2) sum_cost
FROM `xmagnet.spaceone_billing_data_us.gcp_billing_export_v1_01186D_EC0E18_F83B2B`
WHERE DATE(usage_start_time) >= '2025-10-01'
GROUP BY cost_type ORDER BY n DESC
"""
for row in client.query(q).result():
    print(f"  cost_type={row.cost_type!r:<25}  rows={row.n:>9,}  cost_sum={row.sum_cost}")

print()
print("=" * 80)
print("Section E: 我 22 列 vs BQ 实际能给的字段对账")
print("=" * 80)
# 取 native 01186D 的所有顶层字段
t = client.get_table(TABLES[0][1])
bq_top_fields = [f.name for f in t.schema]
print(f"BQ 顶层字段 ({len(bq_top_fields)}): {bq_top_fields}")

OUR_22_COLS = [
    "date", "provider", "project_id", "project_name",
    "service_id", "product", "sku_id", "usage_type",
    "region", "resource_name", "cost_type",
    "usage_quantity", "usage_unit",
    "cost", "cost_at_list",
    "credits_committed", "credits_other", "credits_total",
    "currency", "data_source_id", "tags", "additional_info",
]
print(f"\n我们 22 列: {OUR_22_COLS}")

print()
print("Section F: BQ 有但我们没存的顶层字段（按值对账）")
print("=" * 80)
all_bq_fields = set(f.name for f in t.schema)
# 子字段也展开
def expand_record(field, prefix=""):
    out = []
    if field.field_type == "RECORD":
        for sub in field.fields:
            out.extend(expand_record(sub, prefix + field.name + "."))
    else:
        out.append(prefix + field.name)
    return out

bq_all_paths = []
for f in t.schema:
    bq_all_paths.extend(expand_record(f))

print(f"BQ 完整字段路径数 = {len(bq_all_paths)}")
for p in bq_all_paths:
    # 我们存了哪些
    print(f"  {p}")
