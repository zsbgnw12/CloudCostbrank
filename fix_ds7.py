"""Clean up ds=7 (GCP-us_native, currently is_active=False) historical pollution.

Strategy: temporarily flip is_active=True, re-sync Jan..Apr via the fixed path
so existing bug-residue rows get overwritten with correct SUMs, then flip
is_active back to False.

BQ data for this source comes from the native export table, so the
collector should return real values just like other GCP sources.
"""
import os, sys, time
from pathlib import Path
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

DS_ID = 7


def pg_retry(fn):
    for attempt in range(1, 5):
        try:
            return fn()
        except Exception as ex:
            if "server closed" in repr(ex) and attempt < 4:
                print(f"  (PG flap, retry in {3*attempt}s)")
                time.sleep(3 * attempt)
                continue
            raise


def flip_active(new_val: bool):
    def _work():
        engine = create_engine(os.environ["SYNC_DATABASE_URL"])
        with engine.begin() as c:
            c.execute(text("UPDATE data_sources SET is_active = :v WHERE id = :id"),
                      {"v": new_val, "id": DS_ID})
    pg_retry(_work)
    print(f"  ds={DS_ID} is_active = {new_val}")


def main():
    print(f"=== Step 1: enable ds={DS_ID} temporarily ===")
    flip_active(True)

    print(f"\n=== Step 2: run backfill via the fixed sync path ===")
    # Import here so SYNC_DATABASE_URL is loaded
    import subprocess
    r = subprocess.run(
        [sys.executable, str(ROOT / "backfill_after_dedup_fix.py"),
         "--run", "--ds", str(DS_ID), "--start", "2026-01-01",
         "--end", "2026-04-16", "--month-by-month"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    print(r.stdout)
    if r.stderr:
        print("STDERR:", r.stderr[-500:])
    if r.returncode != 0:
        print(f"!!! backfill exited non-zero ({r.returncode}); leaving is_active=True for you to inspect")
        return

    print(f"\n=== Step 3: verify ds={DS_ID} rows changed ===")
    def _verify():
        engine = create_engine(os.environ["SYNC_DATABASE_URL"])
        with engine.connect() as c:
            rows = c.execute(text("""
                SELECT TO_CHAR(DATE_TRUNC('month', date), 'YYYY-MM') AS mon,
                       COUNT(*) AS n, SUM(cost) AS total
                FROM billing_data WHERE data_source_id = :id
                GROUP BY mon ORDER BY mon
            """), {"id": DS_ID}).fetchall()
        return rows
    for r in pg_retry(_verify):
        print(f"  {r.mon}  rows={r.n:>6}  total=${float(r.total or 0):>12.2f}")

    print(f"\n=== Step 4: restore ds={DS_ID} is_active=False ===")
    flip_active(False)

    print("\n=== Done ===")


if __name__ == "__main__":
    main()
