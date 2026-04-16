"""Why does BQ total 2921 but DB only 156 for same source? Check dedup key collisions."""
import json
from google.cloud import bigquery
from google.oauth2 import service_account

SA_FILE = "c:/Users/陈晨/Desktop/工单相关/newgongdan/cloudcost/xmagnet-c0e170e58dc3.json"
FQT = "cb-export.other.xm"

with open(SA_FILE) as f:
    sa = json.load(f)
cred = service_account.Credentials.from_service_account_info(sa, scopes=["https://www.googleapis.com/auth/cloud-platform"])
client = bigquery.Client(credentials=cred, project=cred.project_id)

# DB dedup key: (date, data_source_id, project_id, product, usage_type, region)
# collector maps: product=service.description, usage_type=sku.description, region=IFNULL(location.region,'global')

# Step 1: raw rows, distinct dedup-keys, SUM(cost), SUM-of-one-per-key (simulating "last-wins" UPSERT)
q = f"""
WITH src AS (
  SELECT
    DATE(usage_start_time) AS d,
    project.id AS pid,
    service.description AS product,
    sku.description AS usage_type,
    IFNULL(location.region, 'global') AS region,
    cost
  FROM `{FQT}`
  WHERE DATE(usage_start_time) BETWEEN '2026-04-01' AND '2026-04-15'
    AND project.id = 'ocid-20260212'
),
by_key AS (
  SELECT d, pid, product, usage_type, region,
         COUNT(*) AS n_rows,
         SUM(cost) AS sum_cost_key,
         ANY_VALUE(cost) AS any_cost_key,
         MAX(cost) AS max_cost_key
  FROM src
  GROUP BY d, pid, product, usage_type, region
)
SELECT
  d,
  SUM(n_rows) AS raw_rows,
  COUNT(*) AS distinct_keys,
  SUM(sum_cost_key) AS sum_all,
  SUM(any_cost_key) AS sum_keep_any,
  SUM(max_cost_key) AS sum_keep_max
FROM by_key
GROUP BY d ORDER BY d
"""
print(f"{'date':<12} {'raw':>6} {'keys':>6} {'sum_all':>12} {'keep_any':>12} {'keep_max':>12}")
for r in client.query(q).result():
    print(f"{r.d!s:<12} {r.raw_rows:>6} {r.distinct_keys:>6} "
          f"{float(r.sum_all):>12.4f} {float(r.sum_keep_any):>12.4f} {float(r.sum_keep_max):>12.4f}")

# Look at one day in detail: show the keys where multiple BQ rows collapse to one DB row
print("\n=== 2026-04-11 dedup-key collisions (top 10) ===")
q2 = f"""
SELECT
  service.description AS product,
  sku.description AS usage_type,
  IFNULL(location.region,'global') AS region,
  COUNT(*) AS n,
  SUM(cost) AS sum_cost,
  MIN(cost) AS min_cost,
  MAX(cost) AS max_cost
FROM `{FQT}`
WHERE DATE(usage_start_time) = '2026-04-11'
  AND project.id = 'ocid-20260212'
GROUP BY product, usage_type, region
HAVING n > 1
ORDER BY sum_cost DESC
LIMIT 10
"""
for r in client.query(q2).result():
    print(f"  n={r.n:>3} sum={float(r.sum_cost):.4f} min={float(r.min_cost):.6f} max={float(r.max_cost):.6f}  {r.product} / {r.usage_type} / {r.region}")
