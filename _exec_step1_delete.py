"""STEP 1 — DELETE CSV rows in single transaction with self-check.
Aborts (ROLLBACK) if delete count != expected 13724."""
raise RuntimeError(
    "billing_data 表已 rename 为 billing_summary，"
    "复用前请把脚本里所有 billing_data 表名改为 billing_summary。"
)
import sys; sys.path.insert(0, ".")
from _db import connect

EXPECTED_ROWS = 13724
EXPECTED_COST_MIN = 239317.0
EXPECTED_COST_MAX = 239319.0
CSV_TS = '2026-04-15 18:51:09.991957'

print("STEP 1 — DELETE CSV rows in transaction\n")
c = connect(readonly=False)
c.autocommit = False
cur = c.cursor()

try:
    # 1. Pre-check
    cur.execute("""SELECT COUNT(*), ROUND(SUM(cost)::numeric,2)
                   FROM billing_data
                   WHERE data_source_id IN (5,6) AND created_at = %s""", (CSV_TS,))
    pre_n, pre_c = cur.fetchone()
    print(f"  pre-check: {pre_n} rows, ${pre_c}")
    if pre_n != EXPECTED_ROWS:
        print(f"  ABORT: expected {EXPECTED_ROWS} rows, got {pre_n}")
        c.rollback(); c.close(); sys.exit(1)

    # 2. DELETE
    cur.execute("""DELETE FROM billing_data
                   WHERE data_source_id IN (5,6) AND created_at = %s""", (CSV_TS,))
    deleted = cur.rowcount
    print(f"  DELETE rowcount: {deleted}")
    if deleted != EXPECTED_ROWS:
        print(f"  ABORT: delete rowcount {deleted} != expected {EXPECTED_ROWS}")
        c.rollback(); c.close(); sys.exit(1)

    # 3. Post-check (inside txn)
    cur.execute("""SELECT COUNT(*) FROM billing_data
                   WHERE data_source_id IN (5,6) AND created_at = %s""", (CSV_TS,))
    remaining = cur.fetchone()[0]
    print(f"  post-check remaining: {remaining}")
    if remaining != 0:
        print(f"  ABORT: {remaining} rows still exist with CSV timestamp")
        c.rollback(); c.close(); sys.exit(1)

    # 4. Make sure we didn't over-delete other ds
    cur.execute("""SELECT data_source_id, COUNT(*)
                   FROM billing_data WHERE data_source_id IN (3,4,5,6,7)
                   GROUP BY data_source_id ORDER BY data_source_id""")
    print("  remaining rows per ds after DELETE:")
    for r in cur.fetchall(): print(f"    ds={r[0]}  rows={r[1]:,}")

    # 5. Commit
    c.commit()
    print("\n  COMMIT done.")
except Exception as e:
    c.rollback()
    print(f"  ERROR: {e}")
    raise
finally:
    c.close()
