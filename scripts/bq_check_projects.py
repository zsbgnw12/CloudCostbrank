"""核对一批 project id 是否在我们 BQ 账单表里有数据。"""
import json
from google.cloud import bigquery
from google.oauth2 import service_account

SA_PATH = "C:/Users/陈晨/Desktop/工单相关/newgongdan/cloudcost/xmagnet-c0e170e58dc3.json"
creds = service_account.Credentials.from_service_account_info(
    json.load(open(SA_PATH)), scopes=["https://www.googleapis.com/auth/cloud-platform"],
)
client = bigquery.Client(credentials=creds, project=creds.project_id)

# 用户问的 10 个 + chuer 的另一种命名变体（无中间-）
TARGETS_RAW = [
    "xianlong-2",
    "chuer-20260218-05",
    "chuer-20260218-04",
    "chuer-20260218-03",
    "chuer-20260218-02",
    "chuer-20260218-01",
    "lyww-04",
    "lyww-03",
    "lyww-02",
    "lyww-01",
]

# 加上无中间-的 chuer 变体一起扫
TARGETS_EXTRA = [
    "chuer-2026021801",
    "chuer-2026021802",
    "chuer-2026021803",
    "chuer-2026021804",
    "chuer-2026021805",
]

ALL_TARGETS = TARGETS_RAW + TARGETS_EXTRA

TABLES = [
    "share-service-nonprod.xmind.billing_report",
    "share-service-nonprod.testmanger.billing_report",
    "cb-export.other.xm",
    "px-billing-report.other.xm",
    "xmagnet.spaceone_billing_data_us.gcp_billing_export_v1_01186D_EC0E18_F83B2B",
]

# 每个 target 在每张表里的命中数 + 时间范围
print(f"{'project':<25} {'table':<70} {'rows':>8} {'date range':<25}")
print("-" * 130)
hits = {p: [] for p in ALL_TARGETS}
for tbl in TABLES:
    try:
        q = f"""
          SELECT project.id AS pid,
                 COUNT(*) AS n,
                 MIN(DATE(usage_start_time)) AS d_min,
                 MAX(DATE(usage_start_time)) AS d_max
          FROM `{tbl}`
          WHERE project.id IN UNNEST(@pids)
            AND DATE(usage_start_time) >= DATE_SUB(CURRENT_DATE(), INTERVAL 365 DAY)
          GROUP BY pid
          ORDER BY pid
        """
        cfg = bigquery.QueryJobConfig(query_parameters=[
            bigquery.ArrayQueryParameter("pids", "STRING", ALL_TARGETS),
        ])
        rs = list(client.query(q, job_config=cfg).result())
        for r in rs:
            hits[r.pid].append((tbl, r.n, r.d_min, r.d_max))
            print(f"{r.pid:<25} {tbl:<70} {r.n:>8} {str(r.d_min)}~{str(r.d_max)}")
    except Exception as e:
        print(f"  {tbl}: ERROR {type(e).__name__}: {str(e)[:120]}")

print()
print("=" * 130)
print("汇总（你问的 10 个）:")
print("=" * 130)
for p in TARGETS_RAW:
    if hits[p]:
        loc = ", ".join(f"{t.split('.')[-2]}.{t.split('.')[-1]} ({n})" for t, n, _, _ in hits[p])
        print(f"  ✓ {p:<25} 命中 {sum(n for _, n, _, _ in hits[p])} 行  ({loc})")
    else:
        # 是 chuer 那种带额外破折号的吗？看变体
        alt = p.replace("-", "", 2)  # 不太对…直接看预备的变体
        # 简单查 chuer-X-Y 是否对应 chuer-XY 的命中
        print(f"  ✗ {p:<25} 没找到")

# 单独看 chuer 变体是否能匹配用户问的
print()
print("注：用户写 `chuer-20260218-05`，BQ 里实际叫 `chuer-2026021805`（无中间破折号）")
for p in TARGETS_EXTRA:
    if hits[p]:
        loc = ", ".join(f"{t.split('.')[-2]}.{t.split('.')[-1]} ({n})" for t, n, _, _ in hits[p])
        print(f"  ✓ {p:<25} 命中 {sum(n for _, n, _, _ in hits[p])} 行  ({loc})")
    else:
        print(f"  ✗ {p:<25} 没找到")
