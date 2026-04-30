"""对 10 份 _billing_model_only.csv 再处理：
1. 时间转 UTC+8（北京时间）
2. 过滤：节省计划=0 且 其他节省=0 的行整行剔除

输出 _billing_model_only_v2.csv（覆盖之前的同名文件不太干净，新加 v2 后缀）
"""
import csv
import datetime as dt
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

OUT_DIR = Path("C:/Users/陈晨/Desktop/工单相关/newgongdan")
START = "2026-04-01"
END = "2026-04-26"
PIDS = [
    "xianlong-2",
    "lyww-01", "lyww-02", "lyww-03", "lyww-04",
    "chuer-2026021801", "chuer-2026021802", "chuer-2026021803",
    "chuer-2026021804", "chuer-2026021805",
]

CST = ZoneInfo("Asia/Shanghai")  # UTC+8


def to_cst(iso_str: str) -> str:
    if not iso_str:
        return ""
    try:
        d = dt.datetime.fromisoformat(iso_str)
    except ValueError:
        return iso_str
    return d.astimezone(CST).strftime("%Y-%m-%d %H:%M:%S %z")


print(f"=== 过滤 + UTC+8 时区转换 ===\n")
print(f"{'project':<22} {'before':>10} {'after':>10} {'kept %':>8}  "
      f"{'cost before':>14} {'cost after':>14}  {'note'}")
print("-" * 110)

total_before_rows = 0
total_after_rows = 0
total_before_cost = Decimal("0")
total_after_cost = Decimal("0")

for pid in PIDS:
    src = OUT_DIR / f"{pid}_{START}_to_{END}_billing_model_only.csv"
    dst = OUT_DIR / f"{pid}_{START}_to_{END}_billing_savings_cst.csv"
    if not src.exists():
        print(f"  [{pid}] 源文件不存在")
        continue

    rows_before = 0
    rows_after = 0
    cost_before = Decimal("0")
    cost_after = Decimal("0")

    with open(src, encoding="utf-8-sig", newline="") as f_in, \
         open(dst, "w", encoding="utf-8-sig", newline="") as f_out:
        rdr = csv.DictReader(f_in)
        writer = csv.DictWriter(f_out, fieldnames=rdr.fieldnames)
        writer.writeheader()
        for row in rdr:
            rows_before += 1
            cost = Decimal(row["费用 ($)"] or "0")
            cost_before += cost

            committed = Decimal(row["节省计划 ($)"] or "0")
            other = Decimal(row["其他节省 ($)"] or "0")

            # 过滤规则：两个都为 0 的行剔除
            if committed == 0 and other == 0:
                continue

            # 时区转换
            row["用量开始"] = to_cst(row["用量开始"])
            row["用量结束"] = to_cst(row["用量结束"])

            writer.writerow(row)
            rows_after += 1
            cost_after += cost

    note = ""
    if rows_after == 0 and rows_before > 0:
        note = "(testmanger view 无 credits → 全过滤掉)"
    elif rows_after > 0:
        note = "(保留有折扣的行)"
    pct = f"{100*rows_after/rows_before:.1f}%" if rows_before else "—"

    print(f"  {pid:<20} {rows_before:>10} {rows_after:>10} {pct:>8}  "
          f"{float(cost_before):>14,.6f} {float(cost_after):>14,.6f}  {note}")

    total_before_rows += rows_before
    total_after_rows += rows_after
    total_before_cost += cost_before
    total_after_cost += cost_after

print("-" * 110)
print(f"  {'TOTAL':<20} {total_before_rows:>10} {total_after_rows:>10} "
      f"{100*total_after_rows/max(total_before_rows,1):>7.1f}%  "
      f"{float(total_before_cost):>14,.6f} {float(total_after_cost):>14,.6f}")
print()
print("DONE. 输出文件: <pid>_2026-04-01_to_2026-04-26_billing_savings_cst.csv")
