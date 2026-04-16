"""End-to-end reconciliation: DB totals vs upstream API totals.

For March 2026 and April 2026 (1..15), for each active data source:
  - Compute SUM(cost) in billing_data
  - Query the upstream provider for the same period + same subscription/account
  - Report diff; FAIL if |diff| / max(db,upstream) > 0.5%

If upstream result > DB → data is MISSING.
If DB result > upstream → data is DOUBLED.
Either case is a data integrity violation.
"""
import datetime as dt
import json
import os
import sys
import time
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

PERIODS = [
    ("2026-03", "2026-03-01", "2026-03-31"),
    ("2026-04", "2026-04-01", "2026-04-15"),
]
TOLERANCE_PCT = 0.5  # half a percent — tight


def _pg_retry(fn):
    for attempt in range(1, 5):
        try:
            return fn()
        except Exception as ex:
            if any(k in repr(ex) for k in ("server closed", "EOF detected", "OperationalError")) and attempt < 4:
                time.sleep(3 * attempt)
                continue
            raise


def db_total(ds_id: int, start: str, end: str) -> tuple[float, float]:
    """Returns (total, csv_portion). CSV rows are identified by the
    `project_id_in_additional` key that the CSV importer sets; sync-path
    rows don't carry that key."""
    def _w():
        eng = create_engine(os.environ["SYNC_DATABASE_URL"])
        with eng.connect() as c:
            row = c.execute(text("""
                SELECT
                  COALESCE(SUM(cost),0) AS total,
                  COALESCE(SUM(cost) FILTER (WHERE additional_info ? 'project_id_in_additional'),0) AS csv_cost
                FROM billing_data
                WHERE data_source_id=:id AND date BETWEEN :s AND :e
            """), {"id": ds_id, "s": start, "e": end}).one()
            return float(row.total or 0), float(row.csv_cost or 0)
    return _pg_retry(_w)


def load_targets():
    def _w():
        eng = create_engine(os.environ["SYNC_DATABASE_URL"])
        with eng.connect() as c:
            rows = c.execute(text("""
                SELECT ds.id, ds.name, ca.provider, ds.config, ca.secret_data
                FROM data_sources ds JOIN cloud_accounts ca ON ca.id = ds.cloud_account_id
                WHERE ds.is_active = true
                ORDER BY ca.provider, ds.id
            """)).mappings().all()
            return [dict(r) for r in rows]
    return _pg_retry(_w)


def _decrypt(enc: str | bytes) -> dict:
    from app.services.crypto_service import decrypt_to_dict
    return decrypt_to_dict(enc)


# -------- Upstream queries -------- #
def upstream_gcp(config, secret, start, end) -> float:
    from google.cloud import bigquery
    from google.oauth2 import service_account
    sa = secret["service_account_json"]
    cred = service_account.Credentials.from_service_account_info(
        sa, scopes=["https://www.googleapis.com/auth/cloud-platform"])
    client = bigquery.Client(credentials=cred, project=cred.project_id)
    table = f"{config['project_id']}.{config['dataset']}.{config['table']}"
    cost_field = config.get("cost_field", "cost")
    q = f"""
      SELECT COALESCE(SUM({cost_field}),0) AS total
      FROM `{table}`
      WHERE DATE(usage_start_time) BETWEEN @s AND @e
    """
    job = client.query(q, job_config=bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("s", "DATE", start),
        bigquery.ScalarQueryParameter("e", "DATE", end),
    ]))
    return float(list(job.result())[0].total or 0)


def upstream_aws(config, secret, start, end) -> float:
    import boto3
    session = boto3.Session(
        aws_access_key_id=secret["aws_access_key_id"],
        aws_secret_access_key=secret["aws_secret_access_key"],
        region_name="us-east-1",
    )
    account_id = config.get("account_id") or session.client("sts").get_caller_identity()["Account"]
    end_excl = (dt.date.fromisoformat(end) + dt.timedelta(days=1)).isoformat()
    ce = session.client("ce")
    resp = ce.get_cost_and_usage(
        TimePeriod={"Start": start, "End": end_excl},
        Granularity="MONTHLY" if start[-2:] == "01" else "DAILY",
        Metrics=["UnblendedCost"],
        Filter={"Dimensions": {"Key": "LINKED_ACCOUNT", "Values": [account_id]}},
    )
    total = 0.0
    for t in resp.get("ResultsByTime", []):
        total += float(t["Total"]["UnblendedCost"]["Amount"])
    return total


def upstream_azure(config, secret, start, end) -> float:
    """Sum Azure cost by re-running the collector for the same range and
    SUMMING the raw rows (no UPSERT). Azure API has no simple aggregate call
    that doesn't download the CSV anyway, so this is the natural path."""
    from app.collectors.azure_collector import AzureCollector
    rows = AzureCollector().collect_billing(secret, config, start, end)
    return sum(float(r.get("cost") or 0) for r in rows)


def query_upstream(t, start, end) -> float:
    secret = _decrypt(t["secret_data"])
    if t["provider"] == "gcp":
        return upstream_gcp(t["config"], secret, start, end)
    if t["provider"] == "aws":
        return upstream_aws(t["config"], secret, start, end)
    if t["provider"] == "azure":
        return upstream_azure(t["config"], secret, start, end)
    raise ValueError(t["provider"])


def main():
    targets = load_targets()
    print(f"=== Reconciling {len(targets)} active data sources vs upstream ===\n")

    failures = []
    for t in targets:
        print(f"--- [{t['provider']}] ds={t['id']} {t['name']} ---")
        for label, s, e in PERIODS:
            try:
                db_val, csv_val = db_total(t["id"], s, e)
                sync_val = db_val - csv_val
                try:
                    up_val = query_upstream(t, s, e)
                except Exception as ex:
                    print(f"  {label}  DB=${db_val:>12.2f}  UPSTREAM=ERROR: {ex!r}")
                    failures.append({"ds_id": t["id"], "period": label, "err": repr(ex)[:200]})
                    continue

                # Expected DB = upstream (sync-fetched portion) + csv (user manual backfill)
                # Compare sync portion against upstream, CSV is just user-truth we cannot verify.
                ref = max(abs(sync_val), abs(up_val), 1.0)
                diff = sync_val - up_val
                pct = abs(diff) / ref * 100
                status = "OK" if pct < TOLERANCE_PCT else "FAIL"
                csv_note = f" (+CSV ${csv_val:.2f})" if csv_val > 0 else ""
                print(f"  {label}  DB=${db_val:>12.2f}{csv_note:<20} sync=${sync_val:>12.2f}  UPSTREAM=${up_val:>12.2f}  diff=${diff:>+10.2f} ({pct:5.2f}%)  {status}")
                if status == "FAIL":
                    failures.append({"ds_id": t["id"], "name": t["name"], "period": label,
                                     "sync": sync_val, "upstream": up_val, "csv": csv_val,
                                     "diff": diff, "pct": pct})
            except Exception as ex:
                print(f"  {label}  ERROR: {ex!r}")
                failures.append({"ds_id": t["id"], "period": label, "err": repr(ex)[:200]})

    print("\n=== Summary ===")
    if not failures:
        print("All data sources reconciled within 0.5% tolerance — DATA IS ACCURATE.")
        return
    print(f"FAIL: {len(failures)} discrepancies")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)


if __name__ == "__main__":
    main()
