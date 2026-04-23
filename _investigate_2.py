"""Investigate two gaps:
A) ds#5 Mar 2026 anomaly ($156k vs BQ $4k)
B) ds#3 missing pre-3/24 data ($13,817)
Read-only."""
import csv, os, psycopg2, json
from collections import defaultdict
from decimal import Decimal

# ---------- DB read-only ----------
c = psycopg2.connect(host="dataope.postgres.database.azure.com", port=5432, user="azuredb",
                     password="h13nYoFJX6QrfLzB8bdipEUCjsZq2P7W", dbname="cloudcost",
                     sslmode="require", connect_timeout=15)
c.set_session(readonly=True); cur = c.cursor()

# ---- A1: ds#5 Mar breakdown from DB
print("### A1. ds#5 DB in 2026-03: by project_id, top 15 ###")
cur.execute("""
SELECT project_id, COUNT(*) n, ROUND(SUM(cost)::numeric,2) tc, MIN(date), MAX(date)
FROM billing_data WHERE data_source_id=5 AND date BETWEEN '2026-03-01' AND '2026-03-31'
GROUP BY project_id ORDER BY tc DESC NULLS LAST LIMIT 15""")
for r in cur.fetchall(): print(f"  {r[0]!r:<35} rows={r[1]:>5} cost=${r[2]:>12}  {r[3]}~{r[4]}")

print("\n### A2. ds#5 DB, group by tags->>billing_account_id (if present) ###")
cur.execute("""
SELECT additional_info->>'billing_account_id' ba, COUNT(*) n, ROUND(SUM(cost)::numeric,2) tc
FROM billing_data WHERE data_source_id=5 AND date BETWEEN '2026-01-01' AND '2026-04-22'
GROUP BY ba ORDER BY tc DESC NULLS LAST""")
for r in cur.fetchall(): print(f"  ba={r[0]!r:<25}  rows={r[1]:>6}  cost=${r[2]}")

print("\n### A3. ds#5 monthly rows — how many were from pre-04-03 CSV import? ###")
cur.execute("""
SELECT TO_CHAR(DATE_TRUNC('month',date),'YYYY-MM') mon,
       COUNT(*) n, ROUND(SUM(cost)::numeric,2) tc,
       SUM(CASE WHEN region IS NULL THEN 1 ELSE 0 END) null_region,
       SUM(CASE WHEN created_at < '2026-04-03' THEN 1 ELSE 0 END) pre_apr3_created
FROM billing_data WHERE data_source_id=5
GROUP BY mon ORDER BY mon""")
for r in cur.fetchall(): print(f"  {r[0]}  rows={r[1]:>6}  cost=${r[2]:>11}  null_region={r[3]:>4}  pre_apr3_created={r[4]:>5}")

print("\n### B1. ds#3 monthly breakdown from DB ###")
cur.execute("""
SELECT TO_CHAR(DATE_TRUNC('month',date),'YYYY-MM') mon, COUNT(*) n, ROUND(SUM(cost)::numeric,2) tc,
       MIN(date), MAX(date)
FROM billing_data WHERE data_source_id=3 GROUP BY mon ORDER BY mon""")
for r in cur.fetchall(): print(f"  {r[0]}  rows={r[1]:>6}  cost=${r[2]:>11}  {r[3]}~{r[4]}")

c.close()

# ---------- B: Inspect CSV file to understand what was imported ----------
print("\n### CSV A1: cost_before_2026-04-03_cb_export_like.csv by month + billing_account ###")
buckets = defaultdict(lambda: {"n":0, "c":Decimal("0")})
ba_count = defaultdict(int)
with open("cost_before_2026-04-03_cb_export_like.csv", encoding="utf-8-sig") as f:
    r = csv.DictReader(f)
    for row in r:
        mon = (row["billed_month"] or row["billed_date"][:7]).strip()
        cost = Decimal(row["cost"] or "0")
        ba = (row.get("billing_account_id") or "").strip()
        buckets[mon]["n"] += 1
        buckets[mon]["c"] += cost
        ba_count[ba] += 1
for mon, v in sorted(buckets.items()):
    print(f"  {mon}  rows={v['n']:>6}  cost=${float(v['c']):>12,.2f}")
print(f"  billing_account distribution: {dict(ba_count)}")

print("\n### CSV B1: cost_before_2026-04-03_px_billing_like.csv ###")
buckets = defaultdict(lambda: {"n":0, "c":Decimal("0")})
ba_count = defaultdict(int)
with open("cost_before_2026-04-03_px_billing_like.csv", encoding="utf-8-sig") as f:
    r = csv.DictReader(f)
    for row in r:
        mon = (row["billed_month"] or row["billed_date"][:7]).strip()
        cost = Decimal(row["cost"] or "0")
        ba = (row.get("billing_account_id") or "").strip()
        buckets[mon]["n"] += 1
        buckets[mon]["c"] += cost
        ba_count[ba] += 1
for mon, v in sorted(buckets.items()):
    print(f"  {mon}  rows={v['n']:>6}  cost=${float(v['c']):>12,.2f}")
print(f"  billing_account distribution: {dict(ba_count)}")
