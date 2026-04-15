"""
导入 cb_export / px_billing 两个数据源的往期 CSV 数据到 billing_data 表。

映射:
  01186D-EC0E18-F83B2B  →  data_source_id = 5  (GCP-cb_export)
  010F18-588F4E-8428CE  →  data_source_id = 6  (GCP-px_billing)
"""

import csv
import json
import sys
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values, Json

from app.config import settings

BATCH_SIZE = 500

PROVIDER_MAP = {
    "google_cloud": "gcp",
    "aws": "aws",
    "azure": "azure",
}

BILLING_ACCOUNT_TO_DS = {
    "01186D-EC0E18-F83B2B": 5,
    "010F18-588F4E-8428CE": 6,
}

CSV_FILES = [
    Path(__file__).resolve().parent.parent / "cost_before_2026-04-03_cb_export_like.csv",
    Path(__file__).resolve().parent.parent / "cost_before_2026-04-03_px_billing_like.csv",
]

INSERT_SQL = """
    INSERT INTO billing_data
        (date, provider, data_source_id, project_id, project_name, product, usage_type, region, cost, usage_quantity, usage_unit, currency, additional_info)
    VALUES %s
    ON CONFLICT (date, data_source_id, project_id, product, usage_type, region)
    DO UPDATE SET
        cost = billing_data.cost + EXCLUDED.cost,
        usage_quantity = billing_data.usage_quantity + EXCLUDED.usage_quantity
"""

TEMPLATE = "(%s, %s, %s, %s, %s, %s, %s, NULL, %s, %s, %s, 'USD', %s)"


def safe_decimal(val: str) -> Decimal:
    try:
        return Decimal(val) if val else Decimal("0")
    except InvalidOperation:
        return Decimal("0")


def parse_date(val: str) -> date:
    parts = val.split("-")
    return date(int(parts[0]), int(parts[1]), int(parts[2]))


def get_dsn():
    """从 SQLAlchemy URL 提取 psycopg2 DSN"""
    url = settings.SYNC_DATABASE_URL
    # postgresql+psycopg2://user:pass@host:port/db?sslmode=require
    url = url.replace("postgresql+psycopg2://", "postgresql://")
    return url


def import_csv(conn, csv_path: Path):
    print(f"\n--- 导入 {csv_path.name} ---", flush=True)
    rows_buf = []
    total = 0
    skipped = 0

    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            billing_account = row["billing_account_id"]
            ds_id = BILLING_ACCOUNT_TO_DS.get(billing_account)
            if ds_id is None:
                skipped += 1
                continue

            additional = {"project_id_in_additional": row.get("project_id_in_additional", "")}

            rows_buf.append((
                parse_date(row["billed_date"]),
                PROVIDER_MAP.get(row["provider"], row["provider"]),
                ds_id,
                row["project_id"] or None,
                row["project_name"] or None,
                row["product"] or None,
                row["usage_type"] or None,
                float(safe_decimal(row["cost"])),
                float(safe_decimal(row["usage_quantity"])),
                row["usage_unit"] or None,
                Json(additional),
            ))

            if len(rows_buf) >= BATCH_SIZE:
                with conn.cursor() as cur:
                    execute_values(cur, INSERT_SQL, rows_buf, template=TEMPLATE)
                conn.commit()
                total += len(rows_buf)
                print(f"  已导入 {total} 条...", flush=True)
                rows_buf.clear()

    if rows_buf:
        with conn.cursor() as cur:
            execute_values(cur, INSERT_SQL, rows_buf, template=TEMPLATE)
        conn.commit()
        total += len(rows_buf)

    print(f"  完成: 共导入 {total} 条, 跳过 {skipped} 条", flush=True)
    return total


def _refresh_summary(conn):
    """Refresh billing_daily_summary for all imported date ranges."""
    print("\n--- 刷新 billing_daily_summary ---", flush=True)
    cur = conn.cursor()
    try:
        cur.execute("SELECT MIN(date), MAX(date) FROM billing_data")
        row = cur.fetchone()
        if not row or row[0] is None:
            print("  无数据, 跳过")
            return
        min_date, max_date = str(row[0]), str(row[1])
        cur.execute(
            "DELETE FROM billing_daily_summary WHERE date >= %s AND date <= %s",
            (min_date, max_date),
        )
        cur.execute("""
            INSERT INTO billing_daily_summary
                (date, provider, data_source_id, project_id, product,
                 total_cost, total_usage, record_count)
            SELECT
                date, provider, data_source_id, project_id, product,
                SUM(cost), SUM(usage_quantity), COUNT(*)
            FROM billing_data
            WHERE date >= %s AND date <= %s
            GROUP BY date, provider, data_source_id, project_id, product
        """, (min_date, max_date))
        conn.commit()
        print(f"  完成: 已刷新 {min_date} ~ {max_date}", flush=True)
    finally:
        cur.close()


def main():
    dsn = get_dsn()
    conn = psycopg2.connect(dsn)
    try:
        grand_total = 0
        for csv_file in CSV_FILES:
            if not csv_file.exists():
                print(f"文件不存在: {csv_file}")
                sys.exit(1)
            grand_total += import_csv(conn, csv_file)

        print(f"\n=== 全部完成, 共导入 {grand_total} 条 ===")

        _refresh_summary(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
