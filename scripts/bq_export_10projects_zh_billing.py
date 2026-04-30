"""导出 10 个项目 4 月 1-26 日 BQ 账单 CSV（中文表头，对齐 GCP 控制台原生账单导出格式）。

字段（13 列）：
  project id | 用量开始 | 用量结束 | 服务说明 | 服务 ID | SKU 说明 | SKU ID
  | 使用量 | 使用量单位 | 费用 ($) | 节省计划 ($) | 其他节省 ($)
  | 未舍入的小计 ($) | 小计 ($)

  - 用 start + end 两列代替单"日期"，保留 BQ 原生小时粒度
  - 节省计划 = COMMITTED_USAGE_DISCOUNT / COMMITTED_USAGE_DISCOUNT_DOLLAR_BASE
  - 其他节省 = 其余 type（PROMOTION / SUSTAINED / RESELLER_MARGIN / FREE_TIER / DISCOUNT 等）
  - 节省金额一律转正数（GCP 原始里 credits.amount 是负数，× -1 输出）
  - 未舍入的小计 = cost_at_list（标价 / 折扣前）
  - 小计 = 费用 - 节省合计（即客户实际支付的钱）

每个项目一份 CSV。
"""

import csv
import datetime as dt
import json
import sys
from collections import defaultdict
from decimal import Decimal
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
START = "2026-04-01"
END = "2026-04-26"

OUT_DIR = Path("C:/Users/陈晨/Desktop/工单相关/newgongdan")

TABLES = [
    "share-service-nonprod.xmind.billing_report",
    "share-service-nonprod.testmanger.billing_report",
    "cb-export.other.xm",
    "px-billing-report.other.xm",
    "xmagnet.spaceone_billing_data_us.gcp_billing_export_v1_01186D_EC0E18_F83B2B",
]

# GCP credits.type 拆分：节省计划 vs 其他节省
COMMITTED_TYPES = {
    "COMMITTED_USAGE_DISCOUNT",
    "COMMITTED_USAGE_DISCOUNT_DOLLAR_BASE",
}

creds = service_account.Credentials.from_service_account_info(
    json.load(open(SA_PATH)),
    scopes=["https://www.googleapis.com/auth/cloud-platform"],
)
client = bigquery.Client(credentials=creds, project=creds.project_id)


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
    return "amount_in_pricing_units"


def has_credits(client, fqt):
    return any(f.name == "credits" for f in client.get_table(fqt).schema)


def has_cost_at_list(client, fqt):
    return any(f.name == "cost_at_list" for f in client.get_table(fqt).schema)


def has_cost(client, fqt):
    return any(f.name == "cost" for f in client.get_table(fqt).schema)


# 1. 先发现每张表里命中哪些 project
print(f"=== Searching {len(PROJECT_IDS)} projects in {len(TABLES)} tables, dates {START} ~ {END} ===")
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
            print(f"  {tbl}:  {len(pids)} pid -> {pids}")
        else:
            print(f"  {tbl}:  (empty)")
    except Exception as e:
        print(f"  {tbl}:  ERROR {e}")

if not table_to_pids:
    print("ERROR: no project found")
    sys.exit(2)


# 2. 对每张表查全量需要字段
all_rows: list[dict] = []
for tbl, pids in table_to_pids.items():
    pf = usage_pricing_field(client, tbl)
    has_cred = has_credits(client, tbl)
    has_cal = has_cost_at_list(client, tbl)
    has_c = has_cost(client, tbl)

    cost_expr = "cost" if has_c else "CAST(NULL AS NUMERIC)"
    cost_at_list_expr = "cost_at_list" if has_cal else "CAST(NULL AS NUMERIC)"
    credits_expr = "TO_JSON_STRING(credits)" if has_cred else "CAST(NULL AS STRING)"

    q = f"""
      SELECT
        project.id AS project_id,
        usage_start_time,
        usage_end_time,
        service.description AS service_desc,
        service.id AS service_id,
        sku.description AS sku_desc,
        sku.id AS sku_id,
        usage.{pf} AS usage_qty,
        usage.pricing_unit AS usage_unit,
        {cost_expr} AS cost,
        {cost_at_list_expr} AS cost_at_list,
        {credits_expr} AS credits_json
      FROM `{tbl}`
      WHERE project.id IN UNNEST(@pids)
        AND DATE(usage_start_time) >= @sd
        AND DATE(usage_start_time) <= @ed
      ORDER BY project_id, usage_start_time, service_id, sku_id
    """
    cfg = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ArrayQueryParameter("pids", "STRING", pids),
        bigquery.ScalarQueryParameter("sd", "STRING", START),
        bigquery.ScalarQueryParameter("ed", "STRING", END),
    ])
    print(f"\nQuerying {tbl} for {len(pids)} pid (pricing_field=usage.{pf})...")
    n = 0
    for row in client.query(q, job_config=cfg).result():
        all_rows.append(dict(row))
        n += 1
    print(f"  fetched {n} rows")


def _credits_split(credits_json: str | None) -> tuple[Decimal, Decimal]:
    """返回 (节省计划金额, 其他节省金额)。原始 credits.amount 是负数，转成正数返回。"""
    if not credits_json or credits_json == "null":
        return Decimal("0"), Decimal("0")
    try:
        arr = json.loads(credits_json)
    except Exception:
        return Decimal("0"), Decimal("0")
    if not isinstance(arr, list):
        return Decimal("0"), Decimal("0")
    committed = Decimal("0")
    other = Decimal("0")
    for c in arr:
        if not isinstance(c, dict):
            continue
        amt = c.get("amount")
        if amt is None:
            continue
        try:
            v = Decimal(str(amt))
        except Exception:
            continue
        # GCP 原始 credits.amount 是负数（折扣金额），转正
        v_abs = -v if v < 0 else v
        ctype = (c.get("type") or "").upper()
        if ctype in COMMITTED_TYPES:
            committed += v_abs
        else:
            other += v_abs
    return committed, other


def _to_num_str(v, places: int = 6) -> str:
    """把 Decimal/None 转成定长小数字符串（避免 0.0001 显示成科学计数法）。"""
    if v is None or v == "":
        return ""
    try:
        d = Decimal(str(v))
    except Exception:
        return str(v)
    return f"{d:.{places}f}"


# 3. 按 project_id 拆分写 CSV
HEADER = [
    "project id",
    "用量开始",
    "用量结束",
    "服务说明",
    "服务 ID",
    "SKU 说明",
    "SKU ID",
    "使用量",
    "使用量单位",
    "费用 ($)",
    "节省计划 ($)",
    "其他节省 ($)",
    "未舍入的小计 ($)",
    "小计 ($)",
]

rows_by_pid: dict[str, list[dict]] = defaultdict(list)
for r in all_rows:
    rows_by_pid[r["project_id"]].append(r)

print(f"\n=== Writing per-project CSV (期间 {START} ~ {END}) ===")
for pid in PROJECT_IDS:
    rows = rows_by_pid.get(pid, [])
    out_path = OUT_DIR / f"{pid}_{START}_to_{END}_billing.csv"

    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        for r in rows:
            committed, other = _credits_split(r.get("credits_json"))

            # 费用：cost 优先；views (testmanger) 没 cost 列时退回 cost_at_list
            cost = r.get("cost")
            if cost is None:
                cost = r.get("cost_at_list") or 0

            cost_at_list = r.get("cost_at_list")
            if cost_at_list is None:
                cost_at_list = cost  # 没标价就拿费用顶替（保持非空）

            # 小计 = 费用 - (节省计划 + 其他节省)（实际支付）
            try:
                cost_dec = Decimal(str(cost or 0))
            except Exception:
                cost_dec = Decimal("0")
            subtotal = cost_dec - committed - other

            ust = r.get("usage_start_time")
            uet = r.get("usage_end_time")
            w.writerow([
                pid,
                ust.isoformat() if ust else "",
                uet.isoformat() if uet else "",
                r.get("service_desc") or "",
                r.get("service_id") or "",
                r.get("sku_desc") or "",
                r.get("sku_id") or "",
                _to_num_str(r.get("usage_qty"), 6),
                r.get("usage_unit") or "",
                _to_num_str(cost, 6),
                _to_num_str(committed, 6),
                _to_num_str(other, 6),
                _to_num_str(cost_at_list, 6),
                _to_num_str(subtotal, 6),
            ])
    size = out_path.stat().st_size
    print(f"  {pid:<22} {len(rows):>8} rows  {size:>12,} bytes  -> {out_path.name}")

print("\nDONE.")
