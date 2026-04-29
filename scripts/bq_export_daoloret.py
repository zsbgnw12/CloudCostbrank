"""导出 daoloret-1 项目 2026-03-01 至今的 BQ 原始账单数据为 CSV。
中文表头格式：中文(英文)。"""

import csv
import datetime as dt
import json
import sys
from pathlib import Path

from google.cloud import bigquery
from google.oauth2 import service_account

SA_PATH = "C:/Users/陈晨/Desktop/工单相关/newgongdan/cloudcost/xmagnet-c0e170e58dc3.json"
PROJECT_ID = "gemini-20251105-b"

# 从 ds#3 数据源配置取的 BQ VIEW（deep-science-1 也在这）
TABLES = [
    "share-service-nonprod.xmind.billing_report",
    "share-service-nonprod.testmanger.billing_report",
    "cb-export.other.xm",
    "px-billing-report.other.xm",
    "xmagnet.spaceone_billing_data_us.gcp_billing_export_v1_01186D_EC0E18_F83B2B",
]

START = "2026-03-01"
END = dt.date.today().isoformat()
OUT_ZH = f"C:/Users/陈晨/Desktop/工单相关/newgongdan/{PROJECT_ID}_{START}_to_{END}_zh.csv"
OUT_EN = f"C:/Users/陈晨/Desktop/工单相关/newgongdan/{PROJECT_ID}_{START}_to_{END}_en.csv"

creds = service_account.Credentials.from_service_account_info(
    json.load(open(SA_PATH)),
    scopes=["https://www.googleapis.com/auth/cloud-platform"],
)
client = bigquery.Client(credentials=creds, project=creds.project_id)


# 先定位项目在哪张表
def find_table(client) -> str:
    for tbl in TABLES:
        try:
            q = f"""
              SELECT COUNT(*) AS n
              FROM `{tbl}`
              WHERE project.id = @pid
                AND DATE(usage_start_time) >= @sd
                AND DATE(usage_start_time) <= @ed
            """
            cfg = bigquery.QueryJobConfig(query_parameters=[
                bigquery.ScalarQueryParameter("pid", "STRING", PROJECT_ID),
                bigquery.ScalarQueryParameter("sd", "STRING", START),
                bigquery.ScalarQueryParameter("ed", "STRING", END),
            ])
            r = next(iter(client.query(q, job_config=cfg).result()))
            if r.n > 0:
                print(f"  Found {r.n} rows in {tbl}")
                return tbl
            print(f"  {tbl}: 0 rows")
        except Exception as e:
            print(f"  {tbl}: query failed: {e}")
    return None


print(f"Searching for project={PROJECT_ID} dates {START}~{END}")
TABLE = find_table(client)
if not TABLE:
    print("ERROR: project not found in any known table")
    sys.exit(2)


# 查询表的 schema 决定有没有可选列
schema_cols = {f.name for f in client.get_table(TABLE).schema}
has_credits = "credits" in schema_cols
has_resource = "resource" in schema_cols
has_cost_at_list = "cost_at_list" in schema_cols
has_cost = "cost" in schema_cols  # ds#3/4 VIEW 没有
has_cost_type = "cost_type" in schema_cols
has_invoice = "invoice" in schema_cols
has_billing_account = "billing_account_id" in schema_cols
has_currency_rate = "currency_conversion_rate" in schema_cols
has_transaction_type = "transaction_type" in schema_cols
has_seller = "seller_name" in schema_cols
has_consumption_model = "consumption_model" in schema_cols
has_system_labels = "system_labels" in schema_cols
has_adjustment_info = "adjustment_info" in schema_cols
has_export_time = "export_time" in schema_cols

# 构建 SELECT 列：BQ 原表是逐行明细（一次用量一行），全部按原列名 SELECT 出来
cols = [
    "billing_account_id" if has_billing_account else "CAST(NULL AS STRING) AS billing_account_id",
    "service.id AS service_id",
    "service.description AS service_description",
    "sku.id AS sku_id",
    "sku.description AS sku_description",
    "usage_start_time",
    "usage_end_time",
    "project.id AS project_id",
    "project.name AS project_name",
    "project.number AS project_number",
    "TO_JSON_STRING(project.labels) AS project_labels",
    "TO_JSON_STRING(labels) AS labels",
    "location.location AS location",
    "location.country AS country",
    "location.region AS region",
    "location.zone AS zone",
    "currency",
    ("currency_conversion_rate" if has_currency_rate else "CAST(NULL AS NUMERIC) AS currency_conversion_rate"),
    "usage.amount AS usage_amount",
    "usage.unit AS usage_unit_raw",
    "usage.amount_in_pricing_units AS usage_amount_in_pricing_units",
    "usage.pricing_unit AS usage_pricing_unit",
    ("cost" if has_cost else "CAST(NULL AS NUMERIC) AS cost"),
    ("cost_at_list" if has_cost_at_list else "CAST(NULL AS NUMERIC) AS cost_at_list"),
    ("cost_type" if has_cost_type else "CAST(NULL AS STRING) AS cost_type"),
    ("TO_JSON_STRING(credits) AS credits" if has_credits else "CAST(NULL AS STRING) AS credits"),
    ("TO_JSON_STRING(adjustment_info) AS adjustment_info" if has_adjustment_info else "CAST(NULL AS STRING) AS adjustment_info"),
    ("invoice.month AS invoice_month" if has_invoice else "CAST(NULL AS STRING) AS invoice_month"),
    ("transaction_type" if has_transaction_type else "CAST(NULL AS STRING) AS transaction_type"),
    ("seller_name" if has_seller else "CAST(NULL AS STRING) AS seller_name"),
    ("consumption_model.id AS consumption_model_id" if has_consumption_model else "CAST(NULL AS STRING) AS consumption_model_id"),
    ("consumption_model.description AS consumption_model_description" if has_consumption_model else "CAST(NULL AS STRING) AS consumption_model_description"),
    ("resource.name AS resource_name" if has_resource else "CAST(NULL AS STRING) AS resource_name"),
    ("resource.global_name AS resource_global_name" if has_resource else "CAST(NULL AS STRING) AS resource_global_name"),
    ("TO_JSON_STRING(system_labels) AS system_labels" if has_system_labels else "CAST(NULL AS STRING) AS system_labels"),
    ("export_time" if has_export_time else "CAST(NULL AS TIMESTAMP) AS export_time"),
]

q = f"""
SELECT {", ".join(cols)}
FROM `{TABLE}`
WHERE project.id = @pid
  AND DATE(usage_start_time) >= @sd
  AND DATE(usage_start_time) <= @ed
ORDER BY usage_start_time, service_id, sku_id
"""

cfg = bigquery.QueryJobConfig(query_parameters=[
    bigquery.ScalarQueryParameter("pid", "STRING", PROJECT_ID),
    bigquery.ScalarQueryParameter("sd", "STRING", START),
    bigquery.ScalarQueryParameter("ed", "STRING", END),
])

print(f"Querying {TABLE}...")
result = client.query(q, job_config=cfg).result()

# 中文(英文) 表头映射
header_map = [
    ("计费账户ID(billing_account_id)", "billing_account_id"),
    ("服务ID(service_id)", "service_id"),
    ("服务说明(service_description)", "service_description"),
    ("SKU ID(sku_id)", "sku_id"),
    ("SKU说明(sku_description)", "sku_description"),
    ("用量开始时间(usage_start_time)", "usage_start_time"),
    ("用量结束时间(usage_end_time)", "usage_end_time"),
    ("项目ID(project_id)", "project_id"),
    ("项目名称(project_name)", "project_name"),
    ("项目编号(project_number)", "project_number"),
    ("项目标签(project_labels)", "project_labels"),
    ("资源标签(labels)", "labels"),
    ("位置(location)", "location"),
    ("国家(country)", "country"),
    ("区域(region)", "region"),
    ("可用区(zone)", "zone"),
    ("币种(currency)", "currency"),
    ("汇率(currency_conversion_rate)", "currency_conversion_rate"),
    ("原始用量(usage_amount)", "usage_amount"),
    ("原始用量单位(usage_unit_raw)", "usage_unit_raw"),
    ("使用量(usage_amount_in_pricing_units)", "usage_amount_in_pricing_units"),
    ("使用量单位(usage_pricing_unit)", "usage_pricing_unit"),
    ("费用(cost)", "cost"),
    ("未舍入的小计(cost_at_list)", "cost_at_list"),
    ("计费类型(cost_type)", "cost_type"),
    ("节省明细(credits)", "credits"),
    ("调整信息(adjustment_info)", "adjustment_info"),
    ("发票月(invoice_month)", "invoice_month"),
    ("交易类型(transaction_type)", "transaction_type"),
    ("销售方(seller_name)", "seller_name"),
    ("消费模式ID(consumption_model_id)", "consumption_model_id"),
    ("消费模式说明(consumption_model_description)", "consumption_model_description"),
    ("资源ID(resource_name)", "resource_name"),
    ("资源全名(resource_global_name)", "resource_global_name"),
    ("系统标签(system_labels)", "system_labels"),
    ("导出时间(export_time)", "export_time"),
]

# 把结果先全量加载（一次查询，写两份 CSV）
all_rows = list(result)
print(f"Fetched {len(all_rows)} rows from BQ")


def _val(row, col):
    v = row.get(col)
    if v is None:
        return ""
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


# 中文(英文) 表头版
with open(OUT_ZH, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow([h for h, _ in header_map])
    for row in all_rows:
        w.writerow([_val(row, col) for _, col in header_map])

# 英文表头版
with open(OUT_EN, "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow([col for _, col in header_map])
    for row in all_rows:
        w.writerow([_val(row, col) for _, col in header_map])

print(f"DONE.")
print(f"  ZH: {OUT_ZH}  ({Path(OUT_ZH).stat().st_size} bytes)")
print(f"  EN: {OUT_EN}  ({Path(OUT_EN).stat().st_size} bytes)")
