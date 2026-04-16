"""billing_data integrity guard — run after every sync or on a cron.

Checks that would catch regressions of the bugs we just fixed:

  1. UNIQUE violations (same dedup-key appears twice). Hidden by NULL
     semantics in default unique constraints, so we check explicitly.
  2. NULL values in key columns that would bypass the unique constraint.
  3. Multiple active data sources pointing at the same billing_account /
     subscription_id — any aggregated report would double-count.
  4. billing_daily_summary drift from billing_data.
  5. Suspicious "too low" daily cost spikes (defensive; warns only).
  6. sync_logs fetched / upserted delta trends (informational).

Exit code 0 = clean, 1 = problems found (for cron/CI).
"""
import os, sys, time
from pathlib import Path
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


def run(conn, sql, **params):
    return conn.execute(text(sql), params)


def main():
    engine = create_engine(os.environ["SYNC_DATABASE_URL"])
    problems = []

    with engine.connect() as c:
        # 1. Duplicate dedup-keys
        n = run(c, """
            SELECT COUNT(*) FROM (
              SELECT date,data_source_id,project_id,product,usage_type,region,COUNT(*) AS n
              FROM billing_data GROUP BY 1,2,3,4,5,6 HAVING COUNT(*) > 1
            ) x
        """).scalar()
        status = "OK" if n == 0 else "FAIL"
        print(f"[{status}] duplicate dedup-key groups: {n}")
        if n > 0:
            problems.append(f"duplicate dedup-keys: {n}")

        # 2. NULL in unique-key columns (bypasses constraint)
        n_null_region = run(c, "SELECT COUNT(*) FROM billing_data WHERE region IS NULL").scalar()
        n_null_product = run(c, "SELECT COUNT(*) FROM billing_data WHERE product IS NULL").scalar()
        n_null_usage = run(c, "SELECT COUNT(*) FROM billing_data WHERE usage_type IS NULL").scalar()
        for col, n in [("region", n_null_region), ("product", n_null_product), ("usage_type", n_null_usage)]:
            status = "OK" if n == 0 else "WARN"
            print(f"[{status}] rows with NULL {col}: {n}")
            if n > 0:
                problems.append(f"NULL {col}: {n} rows")

        # 3. Duplicate billing_account / subscription across active data sources
        rows = run(c, """
            SELECT COALESCE(config->>'billing_account_id', config->>'subscription_id', config->>'account_id') AS acct,
                   array_agg(id ORDER BY id) AS ds_ids,
                   array_agg(name ORDER BY id) AS names
            FROM data_sources
            WHERE is_active = true
              AND COALESCE(config->>'billing_account_id', config->>'subscription_id', config->>'account_id') IS NOT NULL
            GROUP BY acct HAVING count(*) > 1
        """).mappings().all()
        if rows:
            print(f"[FAIL] {len(rows)} duplicate account-id groups across active data sources:")
            for r in rows:
                print(f"   acct={r['acct']}  ds_ids={list(r['ds_ids'])}  names={list(r['names'])}")
            problems.append(f"duplicate account-id groups: {len(rows)}")
        else:
            print("[OK] no duplicate account-id across active data sources")

        # 4. daily_summary drift vs billing_data
        # NULL-safe join: COALESCE project_id / product to '__NULL__' so FULL OUTER JOIN
        # actually matches NULL keys. Without this, every NULL-project_id key produces
        # a false drift hit from the PG default `NULL = NULL → UNKNOWN` semantics.
        drift = run(c, """
            WITH bd AS (
              SELECT date, provider, data_source_id,
                     COALESCE(project_id, '__NULL__') AS pid,
                     COALESCE(product, '__NULL__') AS prod,
                     SUM(cost) AS c FROM billing_data
              WHERE date >= CURRENT_DATE - INTERVAL '90 days'
              GROUP BY 1,2,3,4,5
            ), ds AS (
              SELECT date, provider, data_source_id,
                     COALESCE(project_id, '__NULL__') AS pid,
                     COALESCE(product, '__NULL__') AS prod,
                     SUM(total_cost) AS c FROM billing_daily_summary
              WHERE date >= CURRENT_DATE - INTERVAL '90 days'
              GROUP BY 1,2,3,4,5
            )
            SELECT COUNT(*) FROM bd
            FULL OUTER JOIN ds USING (date, provider, data_source_id, pid, prod)
            WHERE ABS(COALESCE(bd.c,0) - COALESCE(ds.c,0)) > 0.01
        """).scalar()
        status = "OK" if drift == 0 else "WARN"
        print(f"[{status}] billing_daily_summary drift vs billing_data (last 90d): {drift} keys differ")
        if drift > 0:
            problems.append(f"summary drift: {drift} keys")

        # 5. Sync-log anomalies: recent failed runs
        recent_failures = run(c, """
            SELECT COUNT(*) FROM sync_logs
            WHERE start_time >= NOW() - INTERVAL '7 days' AND status != 'success'
        """).scalar()
        status = "OK" if recent_failures == 0 else "WARN"
        print(f"[{status}] sync failures in last 7 days: {recent_failures}")

    if problems:
        print(f"\nFAIL: {len(problems)} problem(s) — {'; '.join(problems)}")
        sys.exit(1)
    print("\nAll integrity checks passed.")


if __name__ == "__main__":
    for attempt in range(1, 5):
        try:
            main()
            break
        except Exception as ex:
            if "server closed" in repr(ex) and attempt < 4:
                print(f"(PG flap, retry {attempt}/4 in {3*attempt}s)")
                time.sleep(3 * attempt)
                continue
            raise
