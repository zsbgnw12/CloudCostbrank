"""导出 10 个项目（xianlong-2, lyww-01~04, chuer-2026021801~05）
2026-03-01 至今的 BQ 原始账单数据为两份 CSV（中文表头 + 英文表头）。

每个项目可能跨多张账单表（cb-export 视图 / xmagnet 原生 / testmanger 视图），
统一查询后按 (table, project_id, usage_start_time) 排序写一份 CSV。
"""

import csv
import datetime as dt
import json
import sys
from pathlib import Path

from google.cloud import bigquery
from google.oauth2 import service_account

SA_PATH = "C:/Users/陈晨/Desktop/工单相关/newgongdan/cloudcost/xmagnet-c0e170e58dc3.json"
PROJECT_IDS = [
    "xianlong-2",
    "lyww-01", "lyww-02", "lyww-03", "lyww-04",
    "chuer-2026021801", "chuer-2026021802", "chuer-2026021803",
    "chuer-2026021804", "chuer-2026021805",
]
START = "2026-03-01"
END = dt.date.today().isoformat()

OUT_DIR = Path("C:/Users/陈晨/Desktop/工单相关/newgongdan")

TABLES = [
    "share-service-nonprod.xmind.billing_report",
    "share-service-nonprod.testmanger.billing_report",
    "cb-export.other.xm",
    "px-billing-report.other.xm",
    "xmagnet.spaceone_billing_data_us.gcp_billing_export_v1_01186D_EC0E18_F83B2B",
]

creds = service_account.Credentials.from_service_account_info(
    json.load(open(SA_PATH)),
    scopes=["https://www.googleapis.com/auth/cloud-platform"],
)
client = bigquery.Client(credentials=creds, project=creds.project_id)


def schema_cols(client, fqt):
    return {f.name for f in client.get_table(fqt).schema}


def usage_pricing_field(client, fqt):
    """部分 VIEW 用单数 amount_in_pricing_unit，部分用复数 amount_in_pricing_units。"""
    tbl = client.get_table(fqt)
    for f in tbl.schema:
        if f.name == "usage" and f.field_type in ("RECORD", "STRUCT"):
            sub = {sf.name for sf in f.fields}
            if "amount_in_pricing_units" in sub:
                return "amount_in_pricing_units"
            if "amount_in_pricing_unit" in sub:
                return "amount_in_pricing_unit"
    return "amount_in_pricing_units"  # default


def build_select(cols, table_label, pricing_field):
    """根据表的 schema 决定取哪些字段，缺失的用 NULL 占位"""
    has = lambda c: c in cols
    parts = []
    parts.append(f"'{table_label}' AS source_table")
    parts.append("billing_account_id" if has("billing_account_id") else "CAST(NULL AS STRING) AS billing_account_id")
    parts.append("service.id AS service_id")
    parts.append("service.description AS service_description")
    parts.append("sku.id AS sku_id")
    parts.append("sku.description AS sku_description")
    parts.append("usage_start_time")
    parts.append("usage_end_time")
    parts.append("project.id AS project_id")
    parts.append("project.name AS project_name")
    parts.append("CAST(project.number AS STRING) AS project_number")
    parts.append("TO_JSON_STRING(labels) AS labels")
    parts.append("location.location AS location")
    parts.append("location.country AS country")
    parts.append("location.region AS region")
    parts.append("location.zone AS zone")
    parts.append("currency")
    parts.append("currency_conversion_rate" if has("currency_conversion_rate") else "CAST(NULL AS NUMERIC) AS currency_conversion_rate")
    parts.append("usage.amount AS usage_amount")
    parts.append("usage.unit AS usage_unit_raw")
    parts.append(f"usage.{pricing_field} AS usage_amount_in_pricing_units")
    parts.append("usage.pricing_unit AS usage_pricing_unit")
    parts.append("cost" if has("cost") else "CAST(NULL AS NUMERIC) AS cost")
    parts.append("cost_at_list" if has("cost_at_list") else "CAST(NULL AS NUMERIC) AS cost_at_list")
    parts.append("cost_type" if has("cost_type") else "CAST(NULL AS STRING) AS cost_type")
    parts.append("TO_JSON_STRING(credits) AS credits" if has("credits") else "CAST(NULL AS STRING) AS credits")
    parts.append("TO_JSON_STRING(adjustment_info) AS adjustment_info" if has("adjustment_info") else "CAST(NULL AS STRING) AS adjustment_info")
    parts.append("invoice.month AS invoice_month" if has("invoice") else "CAST(NULL AS STRING) AS invoice_month")
    parts.append("transaction_type" if has("transaction_type") else "CAST(NULL AS STRING) AS transaction_type")
    parts.append("seller_name" if has("seller_name") else "CAST(NULL AS STRING) AS seller_name")
    parts.append("consumption_model.id AS consumption_model_id" if has("consumption_model") else "CAST(NULL AS STRING) AS consumption_model_id")
    parts.append("consumption_model.description AS consumption_model_description" if has("consumption_model") else "CAST(NULL AS STRING) AS consumption_model_description")
    parts.append("resource.name AS resource_name" if has("resource") else "CAST(NULL AS STRING) AS resource_name")
    parts.append("resource.global_name AS resource_global_name" if has("resource") else "CAST(NULL AS STRING) AS resource_global_name")
    parts.append("TO_JSON_STRING(system_labels) AS system_labels" if has("system_labels") else "CAST(NULL AS STRING) AS system_labels")
    parts.append("export_time" if has("export_time") else "CAST(NULL AS TIMESTAMP) AS export_time")
    return parts


# 中文(英文) header 映射
HEADER_MAP = [
    ("数据源表(source_table)",                       "source_table"),
    ("计费账户ID(billing_account_id)",                "billing_account_id"),
    ("服务ID(service_id)",                            "service_id"),
    ("服务说明(service_description)",                 "service_description"),
    ("SKU ID(sku_id)",                                "sku_id"),
    ("SKU说明(sku_description)",                      "sku_description"),
    ("用量开始时间(usage_start_time)",                "usage_start_time"),
    ("用量结束时间(usage_end_time)",                  "usage_end_time"),
    ("项目ID(project_id)",                            "project_id"),
    ("项目名称(project_name)",                        "project_name"),
    ("项目编号(project_number)",                      "project_number"),
    ("资源标签(labels)",                              "labels"),
    ("位置(location)",                                "location"),
    ("国家(country)",                                 "country"),
    ("区域(region)",                                  "region"),
    ("可用区(zone)",                                  "zone"),
    ("币种(currency)",                                "currency"),
    ("汇率(currency_conversion_rate)",                "currency_conversion_rate"),
    ("原始用量(usage_amount)",                        "usage_amount"),
    ("原始用量单位(usage_unit_raw)",                  "usage_unit_raw"),
    ("使用量(usage_amount_in_pricing_units)",         "usage_amount_in_pricing_units"),
    ("使用量单位(usage_pricing_unit)",                "usage_pricing_unit"),
    ("费用(cost)",                                    "cost"),
    ("未舍入的小计(cost_at_list)",                    "cost_at_list"),
    ("计费类型(cost_type)",                           "cost_type"),
    ("节省明细(credits)",                             "credits"),
    ("调整信息(adjustment_info)",                     "adjustment_info"),
    ("发票月(invoice_month)",                         "invoice_month"),
    ("交易类型(transaction_type)",                    "transaction_type"),
    ("销售方(seller_name)",                           "seller_name"),
    ("消费模式ID(consumption_model_id)",              "consumption_model_id"),
    ("消费模式说明(consumption_model_description)",   "consumption_model_description"),
    ("资源ID(resource_name)",                         "resource_name"),
    ("资源全名(resource_global_name)",                "resource_global_name"),
    ("系统标签(system_labels)",                       "system_labels"),
    ("导出时间(export_time)",                         "export_time"),
]


def _val(row, col):
    v = row.get(col)
    if v is None:
        return ""
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


# 1. 对每张表先 prefilter（哪些 project 在哪张表里有数据）
print(f"=== Searching for {len(PROJECT_IDS)} projects in 5 tables, dates {START} ~ {END} ===")
table_to_pids: dict[str, list[str]] = {}
for tbl in TABLES:
    try:
        q = f"""
          SELECT DISTINCT project.id AS pid
          FROM `{tbl}`
          WHERE project.id IN UNNEST(@pids)
            AND DATE(usage_start_time) >= @sd
            AND DATE(usage_start_time) <= @ed
        """
        cfg = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ArrayQueryParameter("pids", "STRING", PROJECT_IDS),
            bigquery.ScalarQueryParameter("sd", "STRING", START),
            bigquery.ScalarQueryParameter("ed", "STRING", END),
        ])
        rs = list(client.query(q, job_config=cfg).result())
        if rs:
            pids = sorted({r.pid for r in rs})
            table_to_pids[tbl] = pids
            print(f"  {tbl}:  {len(pids)} project(s) → {pids}")
        else:
            print(f"  {tbl}:  (no target project)")
    except Exception as e:
        print(f"  {tbl}:  ERROR {e}")

if not table_to_pids:
    print("ERROR: no target project found anywhere")
    sys.exit(2)

# 2. 拉每张表的全量数据（只对该表有效的 project）
all_rows: list[dict] = []
for tbl, pids in table_to_pids.items():
    label = tbl.split(".")[-2] + "." + tbl.split(".")[-1]
    cols = schema_cols(client, tbl)
    pf = usage_pricing_field(client, tbl)
    print(f"  {label}: pricing field = usage.{pf}")
    select_parts = build_select(cols, table_label=label, pricing_field=pf)
    q = f"""
      SELECT {", ".join(select_parts)}
      FROM `{tbl}`
      WHERE project.id IN UNNEST(@pids)
        AND DATE(usage_start_time) >= @sd
        AND DATE(usage_start_time) <= @ed
      ORDER BY usage_start_time, project_id, service_id, sku_id
    """
    cfg = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ArrayQueryParameter("pids", "STRING", pids),
        bigquery.ScalarQueryParameter("sd", "STRING", START),
        bigquery.ScalarQueryParameter("ed", "STRING", END),
    ])
    print(f"\nQuerying {tbl} for {len(pids)} project(s)...")
    n = 0
    for row in client.query(q, job_config=cfg).result():
        all_rows.append(dict(row))
        n += 1
    print(f"  fetched {n} rows")

# 3. 二次排序（usage_start_time 升序，跨表合并后保持时间顺序）
all_rows.sort(key=lambda r: (str(r.get("usage_start_time") or ""), r.get("project_id") or "", r.get("service_id") or "", r.get("sku_id") or ""))

# 4. 按 project_id 拆分，每个项目一份 CSV（仅英文表头）
from collections import defaultdict
rows_by_pid: dict[str, list[dict]] = defaultdict(list)
for r in all_rows:
    pid = r.get("project_id") or "_unknown"
    rows_by_pid[pid].append(r)

print(f"\n=== Writing per-project CSV ===")
out_files = []
for pid in PROJECT_IDS:
    rows = rows_by_pid.get(pid, [])
    out = OUT_DIR / f"{pid}_{START}_to_{END}.csv"
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([col for _, col in HEADER_MAP])
        for row in rows:
            w.writerow([_val(row, col) for _, col in HEADER_MAP])
    size = out.stat().st_size
    print(f"  {pid:<25} {len(rows):>8} rows  {size:>12,} bytes  → {out.name}")
    out_files.append((pid, len(rows), size, out))

print(f"\nDONE. {len(out_files)} files written to {OUT_DIR}")
