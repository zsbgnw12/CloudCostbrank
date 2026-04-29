"""核对 10 份 CSV 与 BQ 原表的一致性：行数 + 总额 + 字段定义 + 抽样值。"""
import csv
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from google.cloud import bigquery
from google.oauth2 import service_account

SA_PATH = "C:/Users/陈晨/Desktop/工单相关/newgongdan/cloudcost/xmagnet-c0e170e58dc3.json"
OUT_DIR = Path("C:/Users/陈晨/Desktop/工单相关/newgongdan")
START = "2026-03-01"
END = "2026-04-27"

PROJECT_TO_TABLES = {
    "xianlong-2":      ["share-service-nonprod.testmanger.billing_report"],
    "lyww-01":         ["share-service-nonprod.testmanger.billing_report"],
    "lyww-02":         ["share-service-nonprod.testmanger.billing_report"],
    "lyww-03":         ["share-service-nonprod.testmanger.billing_report"],
    "lyww-04":         ["share-service-nonprod.testmanger.billing_report"],
    "chuer-2026021801":["cb-export.other.xm",
                        "xmagnet.spaceone_billing_data_us.gcp_billing_export_v1_01186D_EC0E18_F83B2B"],
    "chuer-2026021802":["cb-export.other.xm",
                        "xmagnet.spaceone_billing_data_us.gcp_billing_export_v1_01186D_EC0E18_F83B2B"],
    "chuer-2026021803":["cb-export.other.xm",
                        "xmagnet.spaceone_billing_data_us.gcp_billing_export_v1_01186D_EC0E18_F83B2B"],
    "chuer-2026021804":["cb-export.other.xm",
                        "xmagnet.spaceone_billing_data_us.gcp_billing_export_v1_01186D_EC0E18_F83B2B"],
    "chuer-2026021805":["cb-export.other.xm",
                        "xmagnet.spaceone_billing_data_us.gcp_billing_export_v1_01186D_EC0E18_F83B2B"],
}

creds = service_account.Credentials.from_service_account_info(
    json.load(open(SA_PATH)), scopes=["https://www.googleapis.com/auth/cloud-platform"],
)
client = bigquery.Client(credentials=creds, project=creds.project_id)


def usage_pricing_field(client, fqt):
    tbl = client.get_table(fqt)
    for f in tbl.schema:
        if f.name == "usage" and f.field_type in ("RECORD", "STRUCT"):
            sub = {sf.name for sf in f.fields}
            if "amount_in_pricing_units" in sub:
                return "amount_in_pricing_units"
            if "amount_in_pricing_unit" in sub:
                return "amount_in_pricing_unit"
    return "amount_in_pricing_units"


# 1. BQ 端聚合 —— 每个项目跨它的所有表查 SUM
print("=" * 110)
print("PART 1: 行数 / cost 总额 / usage_quantity 总额 — BQ 直查 vs CSV 实测")
print("=" * 110)
print(f"{'project':<20} {'BQ rows':>10} {'CSV rows':>10}  {'Δ':>5}  {'BQ cost':>16} {'CSV cost':>16}  {'Δ':>10}  {'BQ qty':>20} {'CSV qty':>20}")
all_ok = True
for pid, tables in PROJECT_TO_TABLES.items():
    bq_rows = 0
    bq_cost = Decimal("0")
    bq_qty = Decimal("0")
    bq_cost_at_list = Decimal("0")
    for tbl in tables:
        pf = usage_pricing_field(client, tbl)
        # cost 字段 testmanger VIEW 没有，要用 cost_at_list
        try:
            tbl_cols = {f.name for f in client.get_table(tbl).schema}
        except Exception as e:
            print(f"  ! get_table {tbl} failed: {e}")
            continue
        cost_expr = "SUM(cost)" if "cost" in tbl_cols else "CAST(0 AS NUMERIC)"
        cost_at_list_expr = "SUM(cost_at_list)" if "cost_at_list" in tbl_cols else "CAST(0 AS NUMERIC)"
        q = f"""
          SELECT COUNT(*) AS n,
                 {cost_expr} AS cost_sum,
                 {cost_at_list_expr} AS cal_sum,
                 SUM(usage.{pf}) AS qty_sum
          FROM `{tbl}`
          WHERE project.id = @pid
            AND DATE(usage_start_time) >= @sd
            AND DATE(usage_start_time) <= @ed
        """
        cfg = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("pid", "STRING", pid),
            bigquery.ScalarQueryParameter("sd", "STRING", START),
            bigquery.ScalarQueryParameter("ed", "STRING", END),
        ])
        r = next(iter(client.query(q, job_config=cfg).result()))
        bq_rows += int(r.n)
        bq_cost += Decimal(str(r.cost_sum or 0))
        bq_cost_at_list += Decimal(str(r.cal_sum or 0))
        bq_qty += Decimal(str(r.qty_sum or 0))

    csv_path = OUT_DIR / f"{pid}_{START}_to_{END}.csv"
    csv_rows = 0
    csv_cost = Decimal("0")
    csv_cost_at_list = Decimal("0")
    csv_qty = Decimal("0")
    with open(csv_path, encoding="utf-8-sig") as f:
        rdr = csv.DictReader(f)
        for r in rdr:
            csv_rows += 1
            csv_cost += Decimal(r["cost"]) if r["cost"] else Decimal("0")
            csv_cost_at_list += Decimal(r["cost_at_list"]) if r["cost_at_list"] else Decimal("0")
            csv_qty += Decimal(r["usage_amount_in_pricing_units"]) if r["usage_amount_in_pricing_units"] else Decimal("0")

    drow = bq_rows - csv_rows
    dcost = bq_cost - csv_cost
    if abs(drow) > 0 or abs(dcost) > Decimal("0.000001"):
        all_ok = False
    flag = "✓" if (drow == 0 and abs(dcost) < Decimal("0.000001")) else "❌"
    # testmanger 没有 cost 列，用 cost_at_list 比对
    if bq_cost == 0 and bq_cost_at_list != 0:
        # CSV cost 也将是 0；改用 cost_at_list 比
        dcost_show = bq_cost_at_list - csv_cost_at_list
        bq_cost_show = bq_cost_at_list
        csv_cost_show = csv_cost_at_list
        cost_label = "(cost_at_list)"
    else:
        dcost_show = dcost
        bq_cost_show = bq_cost
        csv_cost_show = csv_cost
        cost_label = ""
    print(f"  {flag} {pid:<18} {bq_rows:>10} {csv_rows:>10}  {drow:>+5}  {float(bq_cost_show):>16,.6f} {float(csv_cost_show):>16,.6f}  {float(dcost_show):>+10,.6f}  {float(bq_qty):>20,.4f} {float(csv_qty):>20,.4f}  {cost_label}")

# 2. 字段定义比对（测一张 testmanger + 一张 cb-export + 一张 xmagnet native）
print()
print("=" * 110)
print("PART 2: 字段定义对照 — CSV 列 vs BQ schema（每个数据源表抽一张代表）")
print("=" * 110)

# 从 CSV 拿表头
sample_csv = OUT_DIR / "lyww-01_2026-03-01_to_2026-04-27.csv"
with open(sample_csv, encoding="utf-8-sig") as f:
    rdr = csv.reader(f)
    csv_cols = next(rdr)
print(f"CSV 列数: {len(csv_cols)}")
print(f"CSV 列名: {csv_cols}")
print()

CSV_TO_BQ_PATH = {
    "source_table": "(我们加的，标识来源表)",
    "billing_account_id": "billing_account_id",
    "service_id": "service.id",
    "service_description": "service.description",
    "sku_id": "sku.id",
    "sku_description": "sku.description",
    "usage_start_time": "usage_start_time",
    "usage_end_time": "usage_end_time",
    "project_id": "project.id",
    "project_name": "project.name",
    "project_number": "project.number",
    "labels": "labels (REPEATED RECORD, TO_JSON_STRING)",
    "location": "location.location",
    "country": "location.country",
    "region": "location.region",
    "zone": "location.zone",
    "currency": "currency",
    "currency_conversion_rate": "currency_conversion_rate",
    "usage_amount": "usage.amount",
    "usage_unit_raw": "usage.unit",
    "usage_amount_in_pricing_units": "usage.amount_in_pricing_unit(s)",
    "usage_pricing_unit": "usage.pricing_unit",
    "cost": "cost",
    "cost_at_list": "cost_at_list",
    "cost_type": "cost_type",
    "credits": "credits (REPEATED RECORD, TO_JSON_STRING)",
    "adjustment_info": "adjustment_info (RECORD, TO_JSON_STRING)",
    "invoice_month": "invoice.month",
    "transaction_type": "transaction_type",
    "seller_name": "seller_name",
    "consumption_model_id": "consumption_model.id",
    "consumption_model_description": "consumption_model.description",
    "resource_name": "resource.name",
    "resource_global_name": "resource.global_name",
    "system_labels": "system_labels (REPEATED RECORD, TO_JSON_STRING)",
    "export_time": "export_time",
}

REPRESENTATIVE_TABLES = [
    "share-service-nonprod.testmanger.billing_report",
    "cb-export.other.xm",
    "xmagnet.spaceone_billing_data_us.gcp_billing_export_v1_01186D_EC0E18_F83B2B",
]

def schema_paths(client, fqt):
    """返回该表所有字段的 dotted 路径（含 STRUCT 子字段）"""
    paths = []
    def walk(field, prefix=""):
        name = f"{prefix}{field.name}"
        if field.field_type in ("RECORD", "STRUCT"):
            for sub in field.fields:
                walk(sub, prefix=f"{name}.")
        else:
            paths.append(name)
    for f in client.get_table(fqt).schema:
        walk(f)
    return set(paths)

for tbl in REPRESENTATIVE_TABLES:
    print(f"\n--- {tbl} ---")
    paths = schema_paths(client, tbl)
    # 看 usage struct 里是单数还是复数
    if "usage.amount_in_pricing_units" in paths:
        pricing_form = "amount_in_pricing_units (复数)"
    elif "usage.amount_in_pricing_unit" in paths:
        pricing_form = "amount_in_pricing_unit (单数)"
    else:
        pricing_form = "缺失"
    print(f"  usage.<pricing_field> 在此表: {pricing_form}")

    missing = []
    for csv_col, bq_path in CSV_TO_BQ_PATH.items():
        if csv_col == "source_table":
            continue
        # 处理多种说法
        check_paths = []
        if "amount_in_pricing_unit" in bq_path:
            check_paths = ["usage.amount_in_pricing_units", "usage.amount_in_pricing_unit"]
        elif "(REPEATED" in bq_path or "(RECORD" in bq_path:
            base = bq_path.split(" ")[0]
            check_paths = [p for p in paths if p == base or p.startswith(base + ".")]
            check_paths = [base] if check_paths else []
        else:
            check_paths = [bq_path]
        if not any(p in paths or p.startswith("usage.amount_in_pricing_unit") and p in paths for p in check_paths):
            # 更严格：只要其中一个 path 存在就算 hit
            if any(p in paths for p in check_paths):
                continue
            missing.append((csv_col, bq_path))
    if missing:
        print(f"  CSV 字段在该表中没找到对应（这些 cell 在该表来源的行里会是空）:")
        for cc, bp in missing:
            print(f"    {cc:<32} ← {bp}")
    else:
        print(f"  ✓ CSV 全部 36 列都能对应到 BQ 字段")

# 3. 抽样 5 行精确比对（CSV vs BQ 同 (table, project, usage_start_time, sku_id) 的原始行）
print()
print("=" * 110)
print("PART 3: 抽样 5 行原始数据比对（lyww-01 + chuer-2026021801）")
print("=" * 110)

def sample_check(pid, table_label, table_fqt, n=3):
    """从 CSV 取该表来源的前 N 行，去 BQ 直查同一行比对 cost/usage"""
    print(f"\n  [{pid}] from {table_label}:")
    csv_path = OUT_DIR / f"{pid}_{START}_to_{END}.csv"
    pf = usage_pricing_field(client, table_fqt)
    samples = []
    with open(csv_path, encoding="utf-8-sig") as f:
        rdr = csv.DictReader(f)
        for r in rdr:
            if r["source_table"] == table_label:
                samples.append(r)
                if len(samples) >= n:
                    break
    for s in samples:
        # 用 (project, usage_start_time, service.id, sku.id, location.region) 定位 BQ 行
        q = f"""
          SELECT cost, cost_at_list, usage.amount AS usage_amount,
                 usage.{pf} AS usage_pq, sku.id AS sku_id, sku.description AS sku_desc,
                 service.description AS service_desc
          FROM `{table_fqt}`
          WHERE project.id = @pid
            AND usage_start_time = TIMESTAMP(@ust)
            AND sku.id = @skuid
            AND IFNULL(location.region, '') = @region
          LIMIT 1
        """
        cfg = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("pid", "STRING", s["project_id"]),
            bigquery.ScalarQueryParameter("ust", "STRING", s["usage_start_time"]),
            bigquery.ScalarQueryParameter("skuid", "STRING", s["sku_id"]),
            bigquery.ScalarQueryParameter("region", "STRING", s["region"] or ""),
        ])
        try:
            rows = list(client.query(q, job_config=cfg).result())
        except Exception as e:
            print(f"    BQ 查询失败: {e}")
            continue
        if not rows:
            print(f"    ❌ BQ 找不到 (start={s['usage_start_time']}, sku={s['sku_id']})")
            continue
        bq = rows[0]
        csv_cost = Decimal(s["cost"]) if s["cost"] else Decimal("0")
        bq_cost = Decimal(str(bq.cost or 0))
        csv_qty = Decimal(s["usage_amount_in_pricing_units"]) if s["usage_amount_in_pricing_units"] else Decimal("0")
        bq_qty = Decimal(str(bq.usage_pq or 0))
        csv_amt = Decimal(s["usage_amount"]) if s["usage_amount"] else Decimal("0")
        bq_amt = Decimal(str(bq.usage_amount or 0))
        ok_cost = abs(csv_cost - bq_cost) < Decimal("0.000001")
        ok_qty = abs(csv_qty - bq_qty) < Decimal("0.000001")
        ok_amt = abs(csv_amt - bq_amt) < Decimal("0.000001")
        flag_c = "✓" if ok_cost else "❌"
        flag_q = "✓" if ok_qty else "❌"
        flag_a = "✓" if ok_amt else "❌"
        print(f"    {flag_c}{flag_q}{flag_a}  {s['usage_start_time']:<28} sku={s['sku_id']}")
        print(f"          CSV: cost={csv_cost} qty={csv_qty} amount={csv_amt}")
        print(f"          BQ : cost={bq_cost} qty={bq_qty} amount={bq_amt}")

sample_check("lyww-01", "testmanger.billing_report",
             "share-service-nonprod.testmanger.billing_report")
sample_check("chuer-2026021801", "other.xm",
             "cb-export.other.xm")
sample_check("chuer-2026021801",
             "spaceone_billing_data_us.gcp_billing_export_v1_01186D_EC0E18_F83B2B",
             "xmagnet.spaceone_billing_data_us.gcp_billing_export_v1_01186D_EC0E18_F83B2B")

print()
print("=" * 110)
if all_ok:
    print("总结: PART 1 行数和 cost 总额 BQ ↔ CSV 完全一致 ✓")
else:
    print("总结: PART 1 有差异，看上面 ❌ 标注的项目")
print("=" * 110)
