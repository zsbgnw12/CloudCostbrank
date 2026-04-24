"""STEP 0 — record baseline BEFORE any changes. Read-only, with retry."""
import sys; sys.path.insert(0, ".")
from _db import run_q

print("=" * 70)
print("STEP 0 BASELINE — %s" % __import__("datetime").datetime.now(__import__("datetime").UTC).isoformat())
print("=" * 70)

checks = [
    ("Total GCP cost",
     "SELECT ROUND(SUM(cost)::numeric,2) FROM billing_data WHERE provider='gcp'"),
    ("Total GCP rows",
     "SELECT COUNT(*) FROM billing_data WHERE provider='gcp'"),
    ("ds#5",
     "SELECT COUNT(*), ROUND(SUM(cost)::numeric,2) FROM billing_data WHERE data_source_id=5"),
    ("ds#6",
     "SELECT COUNT(*), ROUND(SUM(cost)::numeric,2) FROM billing_data WHERE data_source_id=6"),
    ("ds#7",
     "SELECT COUNT(*), ROUND(SUM(cost)::numeric,2) FROM billing_data WHERE data_source_id=7"),
    ("CSV batch (ds5/6 @ 2026-04-15 18:51:09.991957)",
     """SELECT COUNT(*), ROUND(SUM(cost)::numeric,2) FROM billing_data
        WHERE data_source_id IN (5,6) AND created_at = '2026-04-15 18:51:09.991957'"""),
    ("ds#7 is_active",
     "SELECT is_active FROM data_sources WHERE id=7"),
]
for name, q in checks:
    rows, _ = run_q(q)
    print(f"  {name}: {rows[0] if rows else None}")

print("\nper-month GCP (baseline):")
rows, _ = run_q("""SELECT TO_CHAR(DATE_TRUNC('month',date),'YYYY-MM'), ROUND(SUM(cost)::numeric,2)
                   FROM billing_data WHERE provider='gcp' GROUP BY 1 ORDER BY 1""")
for r in rows: print(f"  {r[0]}  ${r[1]}")
