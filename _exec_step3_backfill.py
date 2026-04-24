"""STEP 3 — Backfill ds#7 from BQ native 01186D.
Bypasses SQLAlchemy engine (flaky on Azure PG idle) — uses psycopg2 with retry.
Mirrors sync_service.upsert_billing_rows: COPY to staging, GROUP BY + SUM,
INSERT ... ON CONFLICT DO UPDATE (= sum overwritten by re-aggregated value)."""
import os, sys, io, json, time
from pathlib import Path
from decimal import Decimal
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

# use app's collector to get BQ data
from app.collectors.gcp_collector import GCPCollector

# use our retry-safe connect
from _db import connect

DS_ID = 7
WINDOWS = [
    ("2025-10-01", "2025-10-31"),
    ("2025-11-01", "2025-11-30"),
    ("2025-12-01", "2025-12-31"),
    ("2026-01-01", "2026-01-31"),
    ("2026-02-01", "2026-02-28"),
    ("2026-03-01", "2026-03-31"),
]

_BILLING_COLUMNS = [
    "date", "provider", "data_source_id", "project_id", "project_name",
    "product", "usage_type", "region", "cost", "usage_quantity",
    "usage_unit", "currency", "tags", "additional_info",
]

def _escape(v):
    if v is None: return "\\N"
    s = str(v)
    s = s.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return s

def upsert_rows_safe(rows):
    """Mirror sync_service.upsert_billing_rows with retry."""
    if not rows: return 0
    buf = io.StringIO()
    for row in rows:
        line = "\t".join(_escape(row.get(c)) for c in _BILLING_COLUMNS)
        buf.write(line + "\n")
    buf.seek(0)
    cols_str = ", ".join(_BILLING_COLUMNS)

    for attempt in range(5):
        try:
            c = connect(readonly=False)
            c.autocommit = False
            cur = c.cursor()
            cur.execute("""CREATE TEMP TABLE _billing_staging (
                date DATE, provider VARCHAR(10), data_source_id INTEGER,
                project_id VARCHAR(200), project_name VARCHAR(200),
                product VARCHAR(200), usage_type VARCHAR(300), region VARCHAR(50),
                cost DECIMAL(20,6), usage_quantity DECIMAL(20,6),
                usage_unit VARCHAR(50), currency VARCHAR(10),
                tags JSONB, additional_info JSONB
            ) ON COMMIT DROP""")
            buf.seek(0)
            cur.copy_expert(
                f"COPY _billing_staging ({cols_str}) FROM STDIN WITH (FORMAT text)", buf)
            cur.execute(f"""
                INSERT INTO billing_data ({cols_str})
                SELECT date, provider, data_source_id, project_id,
                       MAX(project_name), product, usage_type, region,
                       SUM(cost), SUM(usage_quantity),
                       MAX(usage_unit), MAX(currency),
                       (ARRAY_AGG(tags ORDER BY cost DESC))[1],
                       (ARRAY_AGG(additional_info ORDER BY cost DESC))[1]
                FROM _billing_staging
                GROUP BY date, provider, data_source_id, project_id, product, usage_type, region
                ON CONFLICT (date, data_source_id, project_id, product, usage_type, region)
                DO UPDATE SET
                    cost = EXCLUDED.cost, usage_quantity = EXCLUDED.usage_quantity,
                    project_name = EXCLUDED.project_name, currency = EXCLUDED.currency,
                    tags = EXCLUDED.tags, additional_info = EXCLUDED.additional_info
            """)
            c.commit()
            c.close()
            return len(rows)
        except Exception as e:
            print(f"    upsert retry {attempt+1}: {type(e).__name__}: {str(e)[:120]}")
            try: c.close()
            except: pass
            time.sleep(3 * (attempt+1))
    raise RuntimeError("Upsert failed after retries")

# Load ds#7 config + sa from DB (via retry-safe connect)
print(f"STEP 3 — Backfilling ds#{DS_ID} from BQ\n")
c = connect(readonly=True); cur = c.cursor()
cur.execute("""SELECT ds.config, ca.secret_data
               FROM data_sources ds JOIN cloud_accounts ca ON ds.cloud_account_id=ca.id
               WHERE ds.id = %s""", (DS_ID,))
row = cur.fetchone()
c.close()
if not row:
    print("  ABORT: ds#7 not found"); sys.exit(1)
config, secret_blob = row

# Decrypt secret_data. Use app's crypto service directly.
from app.services.crypto_service import decrypt_to_dict
secret_data = decrypt_to_dict(secret_blob)
print(f"  ds#{DS_ID} config: dataset={config.get('dataset')}  table={config.get('table')}")
print(f"    project_id={config.get('project_id')}  cost_field={config.get('cost_field')}  usage_field={config.get('usage_field')}")

collector = GCPCollector()
total_fetched = 0; total_upserted = 0
for start, end in WINDOWS:
    print(f"\n  ── Window {start} ~ {end} ──")
    t0 = time.time()
    rows = collector.collect_billing(secret_data, config, start, end)
    print(f"    BQ returned {len(rows)} rows in {time.time()-t0:.1f}s")

    # normalize same as sync_tasks.py
    for row in rows:
        row["data_source_id"] = DS_ID
        row["provider"] = "gcp"
        if isinstance(row.get("tags"), (dict, list)):
            row["tags"] = json.dumps(row["tags"], ensure_ascii=False)
        elif not row.get("tags"):
            row["tags"] = "{}"
        if isinstance(row.get("additional_info"), (dict, list)):
            row["additional_info"] = json.dumps(row["additional_info"], ensure_ascii=False)
        elif not row.get("additional_info"):
            row["additional_info"] = "{}"

    if rows:
        upserted = upsert_rows_safe(rows)
        print(f"    upserted {upserted} rows")
        total_fetched += len(rows); total_upserted += upserted
    else:
        print("    (empty, skip)")

print(f"\n  === Step 3 done: total fetched={total_fetched}, upserted={total_upserted} ===")

# Quick verify
c = connect(readonly=True); cur = c.cursor()
cur.execute("""SELECT COUNT(*), ROUND(SUM(cost)::numeric,2), MIN(date), MAX(date)
               FROM billing_data WHERE data_source_id = %s""", (DS_ID,))
r = cur.fetchone()
print(f"  DB now: ds#{DS_ID} rows={r[0]:,}  cost=${r[1]}  dates={r[2]}~{r[3]}")
c.close()
