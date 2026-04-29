"""Consolidate CSV-imported NULL-region duplicate rows in billing_data.

Problem:
  import_csv_history.py hardcodes region=NULL in INSERT, and the
  billing_data UNIQUE constraint (date, data_source_id, project_id,
  product, usage_type, region) is not effective against NULL (Postgres
  default: NULL != NULL). So each CSV row inserted as its own record.

Impact today:
  34,172 redundant rows across 4,309 unique-key groups (ds=5/6 only).
  Totals are CORRECT because SUM includes all duplicate rows.

Future risk:
  If sync ever runs for a date/project that has NULL-region CSV rows,
  sync produces region='global' rows that COEXIST with NULL rows →
  DOUBLE COUNTING.

Fix:
  Collapse each NULL-region key group into a single row with
  region='global' and SUM(cost), SUM(usage_quantity).

Safety:
  1. Run within a single transaction
  2. Capture before/after totals per data_source, assert equal
  3. If assertion fails, rollback
"""
raise RuntimeError(
    "billing_data 表已 rename 为 billing_summary，"
    "复用前请把脚本里所有 billing_data 表名改为 billing_summary。"
)
import os, time
from pathlib import Path
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
URL = os.environ["SYNC_DATABASE_URL"]


def q(conn, sql, **params):
    return conn.execute(text(sql), params)


def main(dry_run: bool = True):
    engine = create_engine(URL)

    # === 1. Pre-check totals ===
    with engine.connect() as c:
        pre = q(c, """
            SELECT data_source_id,
                   COUNT(*) AS rows,
                   COUNT(*) FILTER (WHERE region IS NULL) AS null_region_rows,
                   SUM(cost) AS total_cost,
                   SUM(cost) FILTER (WHERE region IS NULL) AS null_region_cost
            FROM billing_data WHERE data_source_id IN (5,6)
            GROUP BY data_source_id ORDER BY data_source_id
        """).mappings().all()
        print("=== Pre-consolidation state ===")
        for r in pre:
            print(f"  ds={r['data_source_id']}  rows={r['rows']}  null_region={r['null_region_rows']}  "
                  f"total=${float(r['total_cost']):.4f}  null_cost=${float(r['null_region_cost'] or 0):.4f}")

    if dry_run:
        print("\n(dry-run: not modifying. Call with dry_run=False to execute.)")
        return

    # === 2. Consolidation in one transaction ===
    with engine.begin() as c:
        # Stage: consolidated rows
        q(c, """
            CREATE TEMP TABLE _consolidated ON COMMIT DROP AS
            SELECT
                date, provider, data_source_id, project_id,
                MAX(project_name) AS project_name,
                product, usage_type,
                'global'::varchar AS region,
                SUM(cost) AS cost,
                SUM(usage_quantity) AS usage_quantity,
                MAX(usage_unit) AS usage_unit,
                MAX(currency) AS currency,
                (ARRAY_AGG(tags ORDER BY cost DESC))[1] AS tags,
                (ARRAY_AGG(additional_info ORDER BY cost DESC))[1] AS additional_info
            FROM billing_data
            WHERE data_source_id IN (5,6) AND region IS NULL
            GROUP BY date, provider, data_source_id, project_id, product, usage_type
        """)
        n_staged = q(c, "SELECT COUNT(*) FROM _consolidated").scalar()
        cost_staged = q(c, "SELECT SUM(cost) FROM _consolidated").scalar() or 0
        print(f"Staged {n_staged} consolidated rows, total cost = ${float(cost_staged):.4f}")

        # Delete NULL-region originals
        n_deleted = q(c, """
            DELETE FROM billing_data
            WHERE data_source_id IN (5,6) AND region IS NULL
        """).rowcount
        print(f"Deleted {n_deleted} NULL-region originals")

        # Insert consolidated rows (using ON CONFLICT DO UPDATE to SUM with any pre-existing 'global' rows)
        q(c, """
            INSERT INTO billing_data
                (date, provider, data_source_id, project_id, project_name,
                 product, usage_type, region, cost, usage_quantity,
                 usage_unit, currency, tags, additional_info)
            SELECT date, provider, data_source_id, project_id, project_name,
                   product, usage_type, region, cost, usage_quantity,
                   usage_unit, currency, tags, additional_info
            FROM _consolidated
            ON CONFLICT (date, data_source_id, project_id, product, usage_type, region)
            DO UPDATE SET
                cost = billing_data.cost + EXCLUDED.cost,
                usage_quantity = billing_data.usage_quantity + EXCLUDED.usage_quantity
        """)

        # Verify total unchanged per data source
        post = q(c, """
            SELECT data_source_id, COUNT(*) AS rows, SUM(cost) AS total_cost
            FROM billing_data WHERE data_source_id IN (5,6)
            GROUP BY data_source_id ORDER BY data_source_id
        """).mappings().all()
        print("=== Post-consolidation state ===")
        for r in post:
            print(f"  ds={r['data_source_id']}  rows={r['rows']}  total=${float(r['total_cost']):.4f}")

        pre_map = {r['data_source_id']: float(r['total_cost']) for r in pre}
        post_map = {r['data_source_id']: float(r['total_cost']) for r in post}
        for ds_id in [5, 6]:
            diff = abs(pre_map[ds_id] - post_map[ds_id])
            if diff > 0.01:
                raise RuntimeError(
                    f"TOTAL MISMATCH for ds={ds_id}: pre=${pre_map[ds_id]:.4f} post=${post_map[ds_id]:.4f} diff=${diff:.4f}"
                )
            print(f"  ds={ds_id}: total preserved (diff=${diff:.6f})")

        # Verify no more dupes
        n_dupes = q(c, """
            SELECT COUNT(*) FROM (
              SELECT date,data_source_id,project_id,product,usage_type,region,COUNT(*) AS n
              FROM billing_data GROUP BY 1,2,3,4,5,6 HAVING COUNT(*) > 1
            ) x
        """).scalar()
        print(f"Remaining duplicate groups in billing_data: {n_dupes}")
        if n_dupes > 0:
            raise RuntimeError(f"Still have {n_dupes} duplicate groups — rolling back")

    # === 3. Refresh daily_summary for touched dates ===
    with engine.begin() as c:
        q(c, """
            DELETE FROM billing_daily_summary
            WHERE data_source_id IN (5,6)
        """)
        q(c, """
            INSERT INTO billing_daily_summary
                (date, provider, data_source_id, project_id, product,
                 total_cost, total_usage, record_count)
            SELECT date, provider, data_source_id, project_id, product,
                   SUM(cost), SUM(usage_quantity), COUNT(*)
            FROM billing_data WHERE data_source_id IN (5,6)
            GROUP BY date, provider, data_source_id, project_id, product
        """)
        print("billing_daily_summary refreshed for ds=5/6")

    print("\n✓ Consolidation complete.")


if __name__ == "__main__":
    import sys
    main(dry_run="--run" not in sys.argv)
