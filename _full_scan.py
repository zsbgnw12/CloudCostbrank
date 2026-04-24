"""Full read-only scan for ds#5 and ds#6 — understand data composition
before deciding what to do. NO WRITES."""
import psycopg2
from collections import defaultdict
from decimal import Decimal

c = psycopg2.connect(host="dataope.postgres.database.azure.com", port=5432, user="azuredb",
                     password="h13nYoFJX6QrfLzB8bdipEUCjsZq2P7W", dbname="cloudcost",
                     sslmode="require", connect_timeout=15)
c.set_session(readonly=True); cur = c.cursor()


def sep(title):
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80)


# 1. High-level: every project in ds#5/6, classified as hash or real
sep("1. All project_ids in ds#5 / ds#6 — classify hash vs real-name")
cur.execute("""
SELECT
  data_source_id,
  CASE WHEN project_id ~ '^project-[0-9a-f]{12}$' THEN 'HASH' ELSE 'REAL' END AS kind,
  project_id,
  COUNT(*) n_rows,
  ROUND(SUM(cost)::numeric, 2) total_cost,
  MIN(date) first_date,
  MAX(date) last_date,
  (SELECT DISTINCT project_name FROM billing_data bd2
     WHERE bd2.data_source_id = billing_data.data_source_id
       AND bd2.project_id = billing_data.project_id
     LIMIT 1) AS sample_project_name
FROM billing_data
WHERE data_source_id IN (5,6)
GROUP BY data_source_id, kind, project_id
ORDER BY data_source_id, total_cost DESC NULLS LAST
""")
print(f"{'ds':>3} {'kind':<5} {'project_id':<28} {'rows':>6} {'cost':>13} {'first':<11} {'last':<11} {'name':<30}")
for r in cur.fetchall():
    print(f"{r[0]:>3} {r[1]:<5} {r[2]!r:<28} {r[3]:>6,} {str(r[4]):>13} {str(r[5]):<11} {str(r[6]):<11} {r[7]!r:<30}")


# 2. The key question: would renaming project-hash → real name cause unique key collisions?
sep("2. Would UPDATE hash → name cause unique-key collisions?")
# For each hash row, construct the target key and see if that key already has a row
cur.execute("""
WITH hashed AS (
  SELECT id, date, data_source_id, project_id, project_name, product, usage_type,
         COALESCE(region, '__NULL__') region_norm, cost
  FROM billing_data
  WHERE data_source_id IN (5,6) AND project_id ~ '^project-[0-9a-f]{12}$'
),
collision AS (
  SELECT h.id AS hash_row_id,
         h.date, h.data_source_id, h.project_name AS target_project_id,
         h.product, h.usage_type, h.region_norm, h.cost AS hash_cost,
         b.id AS conflict_row_id, b.cost AS conflict_cost
  FROM hashed h
  JOIN billing_data b
    ON b.date = h.date
   AND b.data_source_id = h.data_source_id
   AND b.project_id = h.project_name
   AND COALESCE(b.product,'') = COALESCE(h.product,'')
   AND COALESCE(b.usage_type,'') = COALESCE(h.usage_type,'')
   AND COALESCE(b.region,'__NULL__') = h.region_norm
)
SELECT
  data_source_id, target_project_id,
  COUNT(*) n_collisions,
  ROUND(SUM(hash_cost)::numeric, 2) hash_side_cost,
  ROUND(SUM(conflict_cost)::numeric, 2) conflict_side_cost,
  MIN(date) first_day, MAX(date) last_day
FROM collision
GROUP BY data_source_id, target_project_id
ORDER BY data_source_id, n_collisions DESC
""")
rows = cur.fetchall()
if not rows:
    print("  (no collisions — UPDATE is safe without merging)")
else:
    print(f"{'ds':>3} {'target':<28} {'n':>5} {'hash_cost':>12} {'conflict_cost':>14} {'days':<25}")
    total_coll = 0
    for r in rows:
        print(f"{r[0]:>3} {r[1]!r:<28} {r[2]:>5} {str(r[3]):>12} {str(r[4]):>14} {r[5]}~{r[6]}")
        total_coll += r[2]
    print(f"  TOTAL COLLISIONS: {total_coll} rows")


# 3. Same-key duplicate counting — are there already duplicates WITHIN hash or WITHIN real?
sep("3. Existing duplicates within same name-space (sanity check)")
cur.execute("""
SELECT data_source_id, project_id, date, product, usage_type, COALESCE(region,'__NULL__') r,
       COUNT(*) n, ROUND(SUM(cost)::numeric,2) sum_cost
FROM billing_data
WHERE data_source_id IN (5,6)
GROUP BY data_source_id, project_id, date, product, usage_type, r
HAVING COUNT(*) > 1
ORDER BY n DESC LIMIT 10
""")
dup_rows = cur.fetchall()
if not dup_rows:
    print("  (none — unique key is clean)")
else:
    print(f"  {'ds':>3} {'project':<28} {'date':<11} {'product':<30} {'usage_type':<25} {'r':<6} {'n':>3} {'cost':>10}")
    for r in dup_rows: print(f"  {r[0]:>3} {r[1]!r:<28} {str(r[2]):<11} {r[3]!r:<30} {r[4]!r:<25} {r[5]:<6} {r[6]:>3} {r[7]}")


# 4. Monthly summary ds#5 and ds#6 with breakdown hash vs real
sep("4. Monthly ds#5/ds#6 split by hash/real")
cur.execute("""
SELECT data_source_id,
       TO_CHAR(DATE_TRUNC('month', date),'YYYY-MM') mon,
       CASE WHEN project_id ~ '^project-[0-9a-f]{12}$' THEN 'HASH' ELSE 'REAL' END AS kind,
       COUNT(*) n, ROUND(SUM(cost)::numeric,2) cost
FROM billing_data
WHERE data_source_id IN (5,6)
GROUP BY data_source_id, mon, kind
ORDER BY data_source_id, mon, kind
""")
for r in cur.fetchall():
    print(f"  ds={r[0]}  {r[1]}  {r[2]:<5}  rows={r[3]:>6,}  cost=${str(r[4]):>12}")


# 5. What's in additional_info for hash rows?
sep("5. Sample additional_info / project_name for a few hash rows")
cur.execute("""
SELECT id, date, project_id, project_name, additional_info, product, usage_type, cost
FROM billing_data
WHERE data_source_id IN (5,6) AND project_id ~ '^project-[0-9a-f]{12}$'
ORDER BY date DESC
LIMIT 5
""")
for r in cur.fetchall():
    print(f"  id={r[0]} date={r[1]} pid={r[2]!r} pname={r[3]!r} add={r[4]} product={r[5]!r} ut={r[6]!r} cost={r[7]}")


# 6. Total GCP cost by month (is anything else off?)
sep("6. Cross-check: is SUM(cost) per month per ds in billing_daily_summary == billing_data?")
cur.execute("""
SELECT d.data_source_id, TO_CHAR(DATE_TRUNC('month', d.date),'YYYY-MM') mon,
       ROUND(SUM(d.cost)::numeric,2) raw_sum,
       (SELECT ROUND(SUM(total_cost)::numeric,2)
        FROM billing_daily_summary s
        WHERE s.data_source_id = d.data_source_id
          AND DATE_TRUNC('month',s.date) = DATE_TRUNC('month',d.date)) summary_sum
FROM billing_data d
WHERE d.data_source_id IN (3,4,5,6,7) AND d.date >= '2026-01-01'
GROUP BY d.data_source_id, mon
ORDER BY d.data_source_id, mon
""")
print(f"  {'ds':>3} {'mon':<8} {'raw':>13} {'summary':>13} {'diff':>10}")
for r in cur.fetchall():
    raw = r[2] or Decimal("0")
    summ = r[3] or Decimal("0")
    diff = raw - summ
    flag = "" if abs(diff) < Decimal("0.01") else "  ← DRIFT"
    print(f"  {r[0]:>3} {r[1]:<8} {str(raw):>13} {str(summ):>13} {str(diff):>10}{flag}")


# 7. For ds#5/6: any row with data_source_id wrong w.r.t. real billing_account?
sep("7. billing_data rows for ds=5/6, group by additional_info billing_account if any")
cur.execute("""
SELECT data_source_id, additional_info->>'billing_account_id' ba,
       additional_info->>'project_id_in_additional' pia_sample_count,
       COUNT(*) n, ROUND(SUM(cost)::numeric,2) c
FROM billing_data WHERE data_source_id IN (5,6)
GROUP BY data_source_id, ba, pia_sample_count
ORDER BY data_source_id, c DESC NULLS LAST LIMIT 20
""")
for r in cur.fetchall():
    print(f"  ds={r[0]}  ba={r[1]!r}  pia_example={r[2]!r}  rows={r[3]:>6}  cost=${str(r[4])}")


c.close()
print("\n(done, read-only)")
