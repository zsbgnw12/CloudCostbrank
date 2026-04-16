"""Re-sync GCP + Azure data sources after fixing the UPSERT-over-unagg bug.

Why: before the fix, sync_service.upsert_billing_rows used
  SELECT DISTINCT ON (...unique_key...) ORDER BY cost DESC
which silently kept only the single highest-cost row per unique key and
discarded the rest. For providers that return line-item-granularity data
(GCP BQ raw export, Azure Cost Details CSV), one logical SKU/meter on one
day can legitimately be split into 100s of rows — so 99% of real cost was
being dropped at insert time.

After fixing sync_service to GROUP BY + SUM, existing history rows are
still wrong. This script reruns the collectors against the same date
ranges; the fixed ON CONFLICT DO UPDATE will overwrite the wrong totals
with the correct sums.

Usage:
    python backfill_after_dedup_fix.py                             # dry-run
    python backfill_after_dedup_fix.py --run
    python backfill_after_dedup_fix.py --run --providers gcp
    python backfill_after_dedup_fix.py --run --start 2026-01-01 --end 2026-04-15
    python backfill_after_dedup_fix.py --run --ds 3,4,5,6
    python backfill_after_dedup_fix.py --run --month-by-month
"""
import argparse
import calendar
import datetime as dt
import json
import os
import sys
import traceback
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")


def _parse_args():
    today = dt.date.today()
    p = argparse.ArgumentParser()
    p.add_argument("--run", action="store_true", help="actually execute (default: dry-run)")
    p.add_argument("--providers", default="gcp,azure",
                   help="comma list: gcp,azure,aws  (default: gcp,azure)")
    p.add_argument("--ds", default="", help="comma list of data_source ids; overrides --providers")
    p.add_argument("--start", default="2026-01-01", help="YYYY-MM-DD (default 2026-01-01)")
    p.add_argument("--end", default=today.isoformat(), help="YYYY-MM-DD (default today)")
    p.add_argument("--month-by-month", action="store_true",
                   help="sync one calendar month at a time (safer for Azure)")
    return p.parse_args()


def _month_chunks(start: str, end: str):
    s = dt.date.fromisoformat(start)
    e = dt.date.fromisoformat(end)
    cur = s.replace(day=1)
    while cur <= e:
        last = calendar.monthrange(cur.year, cur.month)[1]
        month_end = cur.replace(day=last)
        yield max(cur, s).isoformat(), min(month_end, e).isoformat()
        if cur.month == 12:
            cur = cur.replace(year=cur.year + 1, month=1, day=1)
        else:
            cur = cur.replace(month=cur.month + 1, day=1)


def _sync_one(data_source_id: int, provider: str, config: dict, secret_data: dict,
              start_date: str, end_date: str):
    """Inlined version of tasks.sync_tasks.sync_data_source (no Celery)."""
    from app.collectors import get_collector
    from app.services.sync_service import (
        upsert_billing_rows, refresh_daily_summary, auto_create_gcp_projects,
        create_sync_log, complete_sync_log, update_data_source_sync_status,
    )

    log_id = None
    try:
        log_id = create_sync_log(data_source_id, f"backfill-{dt.datetime.utcnow().isoformat()}",
                                 start_date, end_date)
        update_data_source_sync_status(data_source_id, "running")

        collector = get_collector(provider)
        rows = collector.collect_billing(secret_data, config, start_date, end_date)

        for row in rows:
            row["data_source_id"] = data_source_id
            row["provider"] = provider
            if isinstance(row.get("tags"), (dict, list)):
                row["tags"] = json.dumps(row["tags"], ensure_ascii=False)
            elif not row.get("tags"):
                row["tags"] = "{}"
            if isinstance(row.get("additional_info"), (dict, list)):
                row["additional_info"] = json.dumps(row["additional_info"], ensure_ascii=False)
            elif not row.get("additional_info"):
                row["additional_info"] = "{}"

        upserted = upsert_billing_rows(rows)

        if provider == "gcp":
            try:
                auto_create_gcp_projects(rows)
            except Exception as e:
                print(f"    (warn: auto_create_gcp_projects failed: {e})")

        try:
            refresh_daily_summary(start_date, end_date)
        except Exception as e:
            print(f"    (warn: refresh_daily_summary failed: {e})")

        complete_sync_log(log_id, records_fetched=len(rows), records_upserted=upserted)
        update_data_source_sync_status(data_source_id, "success")
        return len(rows), upserted
    except Exception:
        if log_id is not None:
            try:
                complete_sync_log(log_id, records_fetched=0, records_upserted=0,
                                  error=traceback.format_exc()[-1000:])
            except Exception:
                pass
        update_data_source_sync_status(data_source_id, "failed")
        raise


def main():
    args = _parse_args()

    from sqlalchemy.orm import Session
    from app.services.sync_service import _get_sync_engine
    from app.services.crypto_service import decrypt_to_dict
    from app.models.data_source import DataSource
    from app.models.cloud_account import CloudAccount

    target_providers = {p.strip() for p in args.providers.split(",") if p.strip()}
    target_ds_ids = {int(x) for x in args.ds.split(",") if x.strip()}

    engine = _get_sync_engine()
    targets = []  # (ds_id, name, provider, config, secret_data)
    with Session(engine) as session:
        for ds in session.query(DataSource).filter(DataSource.is_active.is_(True)).all():
            ca = session.get(CloudAccount, ds.cloud_account_id)
            if not ca:
                continue
            if target_ds_ids:
                if ds.id not in target_ds_ids:
                    continue
            else:
                if ca.provider not in target_providers:
                    continue
            try:
                secret = decrypt_to_dict(ca.secret_data) if args.run else {}
            except Exception as e:
                print(f"  (skip ds={ds.id}: cannot decrypt secret: {e})")
                continue
            targets.append((ds.id, ds.name, ca.provider, ds.config, secret))

    if not targets:
        print("No matching data sources.")
        return

    ranges = list(_month_chunks(args.start, args.end)) if args.month_by_month \
        else [(args.start, args.end)]

    print(f"=== Backfill plan ({'DRY RUN' if not args.run else 'EXECUTE'}) ===")
    print(f"Date range: {args.start} ~ {args.end}  "
          f"({'monthly chunks: ' + str(len(ranges)) if args.month_by_month else 'single range'})")
    print(f"Targets ({len(targets)} data sources):")
    for ds_id, name, prov, cfg, _ in targets:
        print(f"  [{prov}] ds={ds_id} name={name}")
        print(f"         config={cfg}")
    print(f"Month-chunks: {ranges}")

    if not args.run:
        print("\n(dry run — pass --run to execute)")
        return

    import time
    total_fetched = total_upserted = 0
    for ds_id, name, prov, cfg, secret in targets:
        for s, e in ranges:
            print(f"\n>>> [{prov}] ds={ds_id} {name}  {s}..{e}")
            for attempt in range(1, 5):
                try:
                    fetched, upserted = _sync_one(ds_id, prov, cfg, secret, s, e)
                    total_fetched += fetched
                    total_upserted += upserted
                    print(f"    fetched={fetched}  upserted={upserted}")
                    break
                except Exception as ex:
                    msg = repr(ex)
                    transient = any(k in msg for k in (
                        "server closed the connection",
                        "OperationalError",
                        "EOF detected",
                        "connection reset",
                        "connection refused",
                    ))
                    if transient and attempt < 4:
                        wait = 5 * attempt
                        print(f"    transient PG error (attempt {attempt}/4), retrying in {wait}s: {msg[:120]}")
                        time.sleep(wait)
                        continue
                    print(f"    FAILED: {msg}")
                    break

    print(f"\n=== Done. fetched={total_fetched}  upserted={total_upserted} ===")


if __name__ == "__main__":
    main()
