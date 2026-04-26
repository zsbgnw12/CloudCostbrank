"""Verify the impact claims of NOT storing service.id / sku.id:
  Claim 2: Different services may share same SKU description
  Claim 3: Cost is unaffected because we SUM
Read-only."""
import sys, json
sys.path.insert(0, ".")
from _db import run_q
from decimal import Decimal
from google.cloud import bigquery
from google.oauth2 import service_account

SA = "c:/Users/陈晨/Desktop/工单相关/newgongdan/cloudcost/xmagnet-c0e170e58dc3.json"
creds = service_account.Credentials.from_service_account_info(
    json.load(open(SA)), scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
bq = bigquery.Client(credentials=creds, project=creds.project_id)

def sep(t): print("\n" + "=" * 78 + "\n  " + t + "\n" + "=" * 78)

# ============================================================================
sep("CLAIM 2: 同一个 sku.description 是否真的会跨多个 service？")
# ============================================================================
# 在 BQ 里找：有没有 sku.description 出现在多个 service 下
q = """
SELECT sku.description AS sku_desc,
       COUNT(DISTINCT service.id) AS n_services,
       ARRAY_AGG(DISTINCT service.description IGNORE NULLS LIMIT 5) AS services,
       ARRAY_AGG(DISTINCT sku.id IGNORE NULLS LIMIT 5) AS sku_ids,
       ROUND(SUM(cost), 2) AS total_cost
FROM `xmagnet.spaceone_billing_data_us.gcp_billing_export_v1_01186D_EC0E18_F83B2B`
WHERE DATE(usage_start_time) >= '2025-10-01'
GROUP BY sku_desc
HAVING n_services > 1
ORDER BY total_cost DESC NULLS LAST
LIMIT 15
"""
rows = list(bq.query(q).result())
if not rows:
    print("  在 ds#7 (01186D) 范围内没有发现 SKU 描述跨多个 service —— claim 2 在这个数据集上不成立")
else:
    print(f"  发现 {len(rows)} 个 sku.description 跨多个 service：")
    for r in rows:
        print(f"    SKU desc: {r.sku_desc!r}")
        print(f"      → 出现在 {r.n_services} 个 service: {list(r.services)}")
        print(f"      → 对应 sku.id: {list(r.sku_ids)}")
        print(f"      → 总金额 ${r.total_cost}")

# 再试更大的样本：share-service-nonprod xmind VIEW
print("\n  -- 在 share-service-nonprod.xmind 数据上再查一次 --")
q2 = """
SELECT sku.description AS sku_desc,
       COUNT(DISTINCT service.id) AS n_services,
       ARRAY_AGG(DISTINCT service.description IGNORE NULLS LIMIT 5) AS services,
       ARRAY_AGG(DISTINCT sku.id IGNORE NULLS LIMIT 5) AS sku_ids,
       ROUND(SUM(cost_at_list), 2) AS total_cost
FROM `share-service-nonprod.xmind.billing_report`
WHERE DATE(usage_start_time) >= '2026-03-01'
GROUP BY sku_desc
HAVING n_services > 1
ORDER BY total_cost DESC NULLS LAST
LIMIT 15
"""
rows2 = list(bq.query(q2).result())
if not rows2:
    print("  也没发现跨 service 的同名 SKU")
else:
    print(f"  发现 {len(rows2)} 个：")
    for r in rows2[:10]:
        print(f"    {r.sku_desc!r}  in {r.n_services} services: {list(r.services)}")

# ============================================================================
sep("CLAIM 3: 不存 sku.id 是否会影响金额？验证 sum-by-description == sum-by-id")
# ============================================================================
# 同一组 (date, project, service.description, sku.description, region)
# vs 同一组 (date, project, service.id, sku.id, region)
# 两种聚合方式总额是否一致？如果 description 唯一对应一个 ID，应该一致
# 如果有 description 复用导致行数不同，金额有差异
q3 = """
WITH by_desc AS (
  SELECT
    DATE(usage_start_time) d,
    project.id pid,
    service.description s,
    sku.description k,
    IFNULL(location.region,'global') r,
    SUM(cost) c
  FROM `xmagnet.spaceone_billing_data_us.gcp_billing_export_v1_01186D_EC0E18_F83B2B`
  WHERE DATE(usage_start_time) BETWEEN '2026-03-01' AND '2026-03-23'
  GROUP BY d, pid, s, k, r
),
by_id AS (
  SELECT
    DATE(usage_start_time) d,
    project.id pid,
    service.id sid,
    service.description s,
    sku.id kid,
    sku.description k,
    IFNULL(location.region,'global') r,
    SUM(cost) c
  FROM `xmagnet.spaceone_billing_data_us.gcp_billing_export_v1_01186D_EC0E18_F83B2B`
  WHERE DATE(usage_start_time) BETWEEN '2026-03-01' AND '2026-03-23'
  GROUP BY d, pid, sid, s, kid, k, r
)
SELECT
  (SELECT COUNT(*) FROM by_desc) n_desc,
  (SELECT COUNT(*) FROM by_id) n_id,
  (SELECT ROUND(SUM(c),4) FROM by_desc) sum_desc,
  (SELECT ROUND(SUM(c),4) FROM by_id) sum_id
"""
r = list(bq.query(q3).result())[0]
print(f"  按 description 聚合行数:  {r.n_desc:,}    总金额: ${r.sum_desc}")
print(f"  按 id 聚合行数:           {r.n_id:,}    总金额: ${r.sum_id}")
print(f"  行数差: {r.n_id - r.n_desc} (id 维度更细)")
print(f"  金额差: ${(r.sum_id or 0) - (r.sum_desc or 0):.4f}")

# ============================================================================
sep("额外检查：DB 现在的 product/usage_type 列是否有重复语义")
# ============================================================================
# DB 里 service.description 同名的产品会被合并；如果原本是 2 个 service 我们现在看成 1 个
# 找：同一 product 是否每个 sku.description 都有冲突
rows, _ = run_q("""
SELECT product, COUNT(DISTINCT usage_type) n_skus,
       ROUND(SUM(cost)::numeric, 2) total
FROM billing_data WHERE provider='gcp' AND data_source_id=7 AND date >= '2025-10-01'
GROUP BY product ORDER BY total DESC NULLS LAST LIMIT 10
""")
print(f"  ds#7 GCP top 10 product (按总成本):")
for r in rows:
    print(f"    {r[0]!r:<40}  skus={r[1]:>4}  cost=${r[2]}")
