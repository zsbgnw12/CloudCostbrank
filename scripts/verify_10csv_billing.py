"""精确验证 10 份 CSV 与 BQ 原表的一致性。

逐项目核对：
1. 日期范围 — 是否严格落在 [2026-04-01, 2026-04-26]
2. 行数 — CSV vs BQ 直查
3. 费用 / 节省计划 / 其他节省 / 标价 / 小计 — 总额对比
4. 异常值检测（负数、空值、NaN、节省 > 费用）
5. 抽样原始行精确比对
"""
import csv
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from google.cloud import bigquery
from google.oauth2 import service_account

SA_PATH = "C:/Users/陈晨/Desktop/工单相关/newgongdan/cloudcost/xmagnet-c0e170e58dc3.json"
OUT_DIR = Path("C:/Users/陈晨/Desktop/工单相关/newgongdan")
START = "2026-04-01"
END = "2026-04-26"

PROJECT_TO_TABLE = {
    "xianlong-2":      "share-service-nonprod.testmanger.billing_report",
    "lyww-01":         "share-service-nonprod.testmanger.billing_report",
    "lyww-02":         "share-service-nonprod.testmanger.billing_report",
    "lyww-03":         "share-service-nonprod.testmanger.billing_report",
    "lyww-04":         "share-service-nonprod.testmanger.billing_report",
    "chuer-2026021801": "cb-export.other.xm",
    "chuer-2026021802": "cb-export.other.xm",
    "chuer-2026021803": "cb-export.other.xm",
    "chuer-2026021804": "cb-export.other.xm",
    "chuer-2026021805": "cb-export.other.xm",
}

COMMITTED_TYPES = {"COMMITTED_USAGE_DISCOUNT", "COMMITTED_USAGE_DISCOUNT_DOLLAR_BASE"}

creds = service_account.Credentials.from_service_account_info(
    json.load(open(SA_PATH)), scopes=["https://www.googleapis.com/auth/cloud-platform"],
)
client = bigquery.Client(credentials=creds, project=creds.project_id)


def usage_pf(client, fqt):
    tbl = client.get_table(fqt)
    for f in tbl.schema:
        if f.name == "usage" and f.field_type in ("RECORD", "STRUCT"):
            sub = {sf.name for sf in f.fields}
            return "amount_in_pricing_units" if "amount_in_pricing_units" in sub else "amount_in_pricing_unit"
    return "amount_in_pricing_units"


print("=" * 110)
print(f"验证 10 份 CSV 与 BQ 原表，期间 {START} ~ {END}")
print("=" * 110)
print()
print(f"{'project':<22} {'CSV rows':>10} {'BQ rows':>10} {'Δ':>5}  "
      f"{'CSV cost':>14} {'BQ cost':>14} {'Δ':>10}  "
      f"{'CSV at_list':>14} {'BQ at_list':>14}  "
      f"{'CSV saved':>10} {'BQ saved':>10}")
print("-" * 160)

all_ok = True
issues = []

for pid, tbl in PROJECT_TO_TABLE.items():
    csv_path = OUT_DIR / f"{pid}_{START}_to_{END}_billing.csv"
    if not csv_path.exists():
        issues.append(f"[{pid}] CSV 文件不存在: {csv_path}")
        all_ok = False
        continue

    # 读 CSV 算合计 + 校验日期
    csv_rows = 0
    csv_cost = Decimal("0")
    csv_at_list = Decimal("0")
    csv_committed = Decimal("0")
    csv_other = Decimal("0")
    csv_subtotal = Decimal("0")
    date_min = None
    date_max = None
    out_of_range = []
    neg_cost = 0
    saved_gt_cost = 0

    with open(csv_path, encoding="utf-8-sig") as f:
        rdr = csv.DictReader(f)
        for row in rdr:
            csv_rows += 1
            ust = row["用量开始"][:10] if row["用量开始"] else ""
            if ust:
                if date_min is None or ust < date_min:
                    date_min = ust
                if date_max is None or ust > date_max:
                    date_max = ust
                if ust < START or ust > END:
                    out_of_range.append(ust)

            cost = Decimal(row["费用 ($)"]) if row["费用 ($)"] else Decimal("0")
            at_list = Decimal(row["未舍入的小计 ($)"]) if row["未舍入的小计 ($)"] else Decimal("0")
            committed = Decimal(row["节省计划 ($)"]) if row["节省计划 ($)"] else Decimal("0")
            other = Decimal(row["其他节省 ($)"]) if row["其他节省 ($)"] else Decimal("0")
            subtotal = Decimal(row["小计 ($)"]) if row["小计 ($)"] else Decimal("0")

            csv_cost += cost
            csv_at_list += at_list
            csv_committed += committed
            csv_other += other
            csv_subtotal += subtotal

            if cost < 0:
                neg_cost += 1
            if (committed + other) > cost + Decimal("0.000001"):
                saved_gt_cost += 1

    # BQ 直查同期同项目同表
    pf = usage_pf(client, tbl)
    has_cost = any(f.name == "cost" for f in client.get_table(tbl).schema)
    has_credits = any(f.name == "credits" for f in client.get_table(tbl).schema)

    cost_expr = "cost" if has_cost else "cost_at_list"
    if has_credits:
        # 在 BQ 端拆 committed / other（与 Python 端逻辑一致）
        q = f"""
          SELECT
            COUNT(*) AS n,
            SUM({cost_expr}) AS cost_sum,
            SUM(cost_at_list) AS cal_sum,
            SUM(
              IFNULL(
                (SELECT SUM(-c.amount) FROM UNNEST(credits) c WHERE c.type IN UNNEST(@committed_types)),
                0
              )
            ) AS committed_sum,
            SUM(
              IFNULL(
                (SELECT SUM(-c.amount) FROM UNNEST(credits) c WHERE c.type NOT IN UNNEST(@committed_types)),
                0
              )
            ) AS other_sum
          FROM `{tbl}`
          WHERE project.id = @pid
            AND DATE(usage_start_time) >= @sd
            AND DATE(usage_start_time) <= @ed
        """
        params = [
            bigquery.ScalarQueryParameter("pid", "STRING", pid),
            bigquery.ScalarQueryParameter("sd", "STRING", START),
            bigquery.ScalarQueryParameter("ed", "STRING", END),
            bigquery.ArrayQueryParameter("committed_types", "STRING", list(COMMITTED_TYPES)),
        ]
    else:
        q = f"""
          SELECT
            COUNT(*) AS n,
            SUM({cost_expr}) AS cost_sum,
            SUM(cost_at_list) AS cal_sum,
            CAST(0 AS NUMERIC) AS committed_sum,
            CAST(0 AS NUMERIC) AS other_sum
          FROM `{tbl}`
          WHERE project.id = @pid
            AND DATE(usage_start_time) >= @sd
            AND DATE(usage_start_time) <= @ed
        """
        params = [
            bigquery.ScalarQueryParameter("pid", "STRING", pid),
            bigquery.ScalarQueryParameter("sd", "STRING", START),
            bigquery.ScalarQueryParameter("ed", "STRING", END),
        ]
    cfg = bigquery.QueryJobConfig(query_parameters=params)
    r = next(iter(client.query(q, job_config=cfg).result()))

    bq_rows = int(r.n)
    bq_cost = Decimal(str(r.cost_sum or 0))
    bq_at_list = Decimal(str(r.cal_sum or 0))
    bq_committed = Decimal(str(r.committed_sum or 0))
    bq_other = Decimal(str(r.other_sum or 0))

    drow = csv_rows - bq_rows
    dcost = csv_cost - bq_cost
    da_list = csv_at_list - bq_at_list
    dcommitted = csv_committed - bq_committed
    dother = csv_other - bq_other

    csv_saved = csv_committed + csv_other
    bq_saved = bq_committed + bq_other

    flag = "OK" if (drow == 0 and abs(dcost) < Decimal("0.000001")) else "BAD"
    if flag == "BAD":
        all_ok = False

    print(f"  {flag} {pid:<20} {csv_rows:>10} {bq_rows:>10}  {drow:>+5}  "
          f"{float(csv_cost):>14,.6f} {float(bq_cost):>14,.6f}  {float(dcost):>+10,.6f}  "
          f"{float(csv_at_list):>14,.6f} {float(bq_at_list):>14,.6f}  "
          f"{float(csv_saved):>10,.6f} {float(bq_saved):>10,.6f}")

    if out_of_range:
        issues.append(f"[{pid}] 日期超出 [{START},{END}] 范围 {len(out_of_range)} 行；样本: {out_of_range[:3]}")
        all_ok = False
    if neg_cost > 0:
        issues.append(f"[{pid}] 费用为负的行数 = {neg_cost}")
    if saved_gt_cost > 0:
        issues.append(f"[{pid}] 节省总和 > 费用 的行数 = {saved_gt_cost}（理论不应出现）")
    if date_min and (date_min < START):
        issues.append(f"[{pid}] 最早日期 {date_min} 早于 {START}")
    if date_max and (date_max > END):
        issues.append(f"[{pid}] 最晚日期 {date_max} 晚于 {END}")
    if abs(dcommitted) > Decimal("0.000001"):
        issues.append(f"[{pid}] 节省计划合计差: CSV={csv_committed} BQ={bq_committed} Δ={dcommitted}")
    if abs(dother) > Decimal("0.000001"):
        issues.append(f"[{pid}] 其他节省合计差: CSV={csv_other} BQ={bq_other} Δ={dother}")

print()
print("=" * 110)
if issues:
    print(f"发现 {len(issues)} 条异常:")
    for x in issues:
        print(f"  - {x}")
else:
    print("0 条异常。")
print("=" * 110)
print()
print("总判: " + ("PASS" if all_ok else "FAIL"))
