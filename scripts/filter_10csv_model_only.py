"""把 10 个项目的 _billing.csv 过滤成"仅模型相关"，剔除基础设施。

白名单：service 描述 = 'Vertex AI'（也兼容大小写 / 前后空格）
其它如 Cloud Logging / Cloud Monitoring / Cloud Storage / Compute Engine /
networking / BigQuery / Cloud Run 等基础设施一律剔除。

输出新文件：{pid}_..._billing_model_only.csv
原 _billing.csv 保留不动。
"""
import csv
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

OUT_DIR = Path("C:/Users/陈晨/Desktop/工单相关/newgongdan")
START = "2026-04-01"
END = "2026-04-26"
PIDS = [
    "xianlong-2",
    "lyww-01", "lyww-02", "lyww-03", "lyww-04",
    "chuer-2026021801", "chuer-2026021802", "chuer-2026021803",
    "chuer-2026021804", "chuer-2026021805",
]

# 白名单：模型服务（按 service.description 匹配）
WHITELIST = {"Vertex AI"}

# 如果未来出现其它模型服务名，加到这里：
# 候选：'Generative Language API', 'AI Platform', 'AutoML', 'Cloud Translation',
#       'Cloud Vision API', 'Cloud Natural Language API', 'Speech-to-Text', 'Text-to-Speech'
# 当前 10 项目 4 月数据里只出现 Vertex AI，所以白名单就这一个。

print(f"=== 过滤 10 项目（白名单 service: {WHITELIST}）===\n")
print(f"{'project':<22} {'before':>10} {'after':>10} {'dropped':>10}  "
      f"{'cost before':>14} {'cost after':>14} {'cost dropped':>14}")
print("-" * 110)

total_before_rows = 0
total_after_rows = 0
total_before_cost = Decimal("0")
total_after_cost = Decimal("0")
dropped_services = defaultdict(lambda: {"rows": 0, "cost": Decimal("0")})

for pid in PIDS:
    src = OUT_DIR / f"{pid}_{START}_to_{END}_billing.csv"
    dst = OUT_DIR / f"{pid}_{START}_to_{END}_billing_model_only.csv"
    if not src.exists():
        print(f"  [{pid}] 源文件不存在: {src}")
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
            svc = (row["服务说明"] or "").strip()
            if svc in WHITELIST:
                writer.writerow(row)
                rows_after += 1
                cost_after += cost
            else:
                dropped_services[svc]["rows"] += 1
                dropped_services[svc]["cost"] += cost

    rows_dropped = rows_before - rows_after
    cost_dropped = cost_before - cost_after
    total_before_rows += rows_before
    total_after_rows += rows_after
    total_before_cost += cost_before
    total_after_cost += cost_after

    print(f"  {pid:<20} {rows_before:>10} {rows_after:>10} {rows_dropped:>10}  "
          f"{float(cost_before):>14,.6f} {float(cost_after):>14,.6f} "
          f"{float(cost_dropped):>14,.6f}")

print("-" * 110)
print(f"  {'TOTAL':<20} {total_before_rows:>10} {total_after_rows:>10} "
      f"{total_before_rows-total_after_rows:>10}  "
      f"{float(total_before_cost):>14,.6f} {float(total_after_cost):>14,.6f} "
      f"{float(total_before_cost-total_after_cost):>14,.6f}")
print()
print("=== 被剔除的服务（合计）===")
for svc, v in sorted(dropped_services.items(), key=lambda x: -x[1]["cost"]):
    print(f"  {svc:<35} {v['rows']:>8} 行  ${float(v['cost']):>14,.6f}")
print()
print("DONE. 文件命名: <pid>_2026-04-01_to_2026-04-26_billing_model_only.csv")
