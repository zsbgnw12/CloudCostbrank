"""验证 export-full CSV 内容是否和 BQ 源数据一致。
取 deep-science-1 4 月数据，从 CSV 读 + BQ 直查 + 逐项对比。
Read-only."""
import csv, json
from collections import defaultdict
from decimal import Decimal
from google.cloud import bigquery
from google.oauth2 import service_account

CSV_PATH = "c:/Users/陈晨/Desktop/工单相关/newgongdan/deep-science-1_2026-04_full.csv"
SA = "c:/Users/陈晨/Desktop/工单相关/newgongdan/cloudcost/xmagnet-c0e170e58dc3.json"
PROJECT = "deep-science-1"
START, END = "2026-04-01", "2026-04-26"  # CSV 里也是 4-1 到 4-26

# ds#3 → xmind VIEW
BQ_TABLE = "share-service-nonprod.xmind.billing_report"

creds = service_account.Credentials.from_service_account_info(
    json.load(open(SA)), scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
client = bigquery.Client(credentials=creds, project=creds.project_id)

# 1. 读 CSV，按 (date, service_id, sku_id, region) 聚合
csv_data = defaultdict(lambda: {"cost": Decimal("0"), "cost_at_list": Decimal("0"),
                                 "qty": Decimal("0"), "n": 0,
                                 "billing_account_id": None, "invoice_month": None,
                                 "service_desc": None, "sku_desc": None})
with open(CSV_PATH, encoding="utf-8") as f:
    rdr = csv.DictReader(f)
    for r in rdr:
        key = (r["date"], r["service_id"], r["sku_id"], r["region"], r["cost_type"])
        d = csv_data[key]
        d["cost"] += Decimal(r["cost"] or "0")
        d["cost_at_list"] += Decimal(r["cost_at_list"] or "0")
        d["qty"] += Decimal(r["usage_quantity"] or "0")
        d["n"] += 1
        d["billing_account_id"] = r["billing_account_id"]
        d["invoice_month"] = r["invoice_month"]
        d["service_desc"] = r["product"]
        d["sku_desc"] = r["usage_type"]

print(f"CSV: {len(csv_data)} 个 unique key, project={PROJECT}, 期间 {START}~{END}")

# 2. BQ 直查同一 project 同一时段，按相同 key GROUP BY
q = f"""
SELECT
  CAST(DATE(usage_start_time) AS STRING) AS date,
  ANY_VALUE(service.id) AS service_id,
  service.description AS service_desc,
  ANY_VALUE(sku.id) AS sku_id,
  sku.description AS sku_desc,
  IFNULL(location.region, 'global') AS region,
  IFNULL(cost_type, 'regular') AS cost_type,
  ANY_VALUE(billing_account_id) AS billing_account_id,
  ANY_VALUE(invoice.month) AS invoice_month,
  SUM(cost_at_list) AS cost_at_list,
  SUM(usage.amount_in_pricing_unit) AS qty,
  COUNT(*) AS n
FROM `{BQ_TABLE}`
WHERE project.id = '{PROJECT}'
  AND DATE(usage_start_time) BETWEEN '{START}' AND '{END}'
GROUP BY date, service_desc, sku_desc, region, cost_type
"""

bq_data = {}
for r in client.query(q).result():
    key = (r.date, r.service_id, r.sku_id, r.region, r.cost_type)
    bq_data[key] = {
        "cost_at_list": Decimal(str(r.cost_at_list)),
        "qty": Decimal(str(r.qty or 0)),
        "n_raw_lines": r.n,
        "billing_account_id": r.billing_account_id,
        "invoice_month": r.invoice_month,
        "service_desc": r.service_desc,
        "sku_desc": r.sku_desc,
    }
print(f"BQ:  {len(bq_data)} 个 unique key")

# 3. 对比
print()
print("=" * 78)
print("Comparison")
print("=" * 78)
csv_keys = set(csv_data.keys())
bq_keys = set(bq_data.keys())
only_csv = csv_keys - bq_keys
only_bq = bq_keys - csv_keys
both = csv_keys & bq_keys

print(f"  在 CSV 但 BQ 没有: {len(only_csv)}")
print(f"  在 BQ 但 CSV 没有: {len(only_bq)}")
print(f"  两边都有: {len(both)}")

# 抽样 5 个差异
if only_csv:
    print("\n  CSV-only 样本（可能 BQ 数据已变 / 滚动窗口）:")
    for k in list(only_csv)[:5]: print(f"    {k}  cost={csv_data[k]['cost']}")
if only_bq:
    print("\n  BQ-only 样本（CSV 漏了？）:")
    for k in list(only_bq)[:5]: print(f"    {k}  cost_at_list={bq_data[k]['cost_at_list']}")

# 4. 对应 key 的金额对比
print()
print("=" * 78)
print("Amount diff (both sides) — 取前 10 个差异")
print("=" * 78)
diffs = []
for k in both:
    csv_c = csv_data[k]["cost_at_list"]
    bq_c = bq_data[k]["cost_at_list"]
    diff = abs(csv_c - bq_c)
    if diff >= Decimal("0.001"):
        diffs.append((k, csv_c, bq_c, diff))
diffs.sort(key=lambda x: -x[3])
print(f"  有差异的 key 数: {len(diffs)}")
for k, csv_c, bq_c, d in diffs[:10]:
    print(f"    {k}")
    print(f"      CSV cost_at_list = {csv_c}, BQ cost_at_list = {bq_c}, diff = {d}")

# 5. 总额对账
print()
print("=" * 78)
print("总额对账")
print("=" * 78)
csv_total = sum(d["cost_at_list"] for d in csv_data.values())
bq_total = sum(d["cost_at_list"] for d in bq_data.values())
print(f"  CSV total cost_at_list = ${csv_total:,.2f}")
print(f"  BQ  total cost_at_list = ${bq_total:,.2f}")
print(f"  diff: ${csv_total - bq_total:.2f} ({100*abs(csv_total-bq_total)/max(csv_total,Decimal('0.01')):.4f}%)")

# 6. 字段值校验：随便取 5 个 key 比较 billing_account / invoice_month / desc 是否一致
print()
print("=" * 78)
print("字段值一致性（5 个抽样）")
print("=" * 78)
for i, k in enumerate(list(both)[:5]):
    c, b = csv_data[k], bq_data[k]
    print(f"  Sample {i+1}: {k}")
    for field in ["billing_account_id", "invoice_month", "service_desc", "sku_desc"]:
        cv = c[field]
        bv = b[field]
        flag = "OK " if cv == bv else "DIFF"
        print(f"    {flag}  {field}: CSV={cv!r}  BQ={bv!r}")
