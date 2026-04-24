"""FINAL AUDIT — comprehensive integrity + alignment check after cleanup.
Read-only. No writes."""
import sys; sys.path.insert(0, ".")
from _db import run_q
from decimal import Decimal
import json
from google.cloud import bigquery
from google.oauth2 import service_account

SA = "c:/Users/陈晨/Desktop/工单相关/newgongdan/cloudcost/xmagnet-c0e170e58dc3.json"
creds = service_account.Credentials.from_service_account_info(
    json.load(open(SA)), scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
bq = bigquery.Client(credentials=creds, project=creds.project_id)

fail_count = 0
def check(name, cond, detail=""):
    global fail_count
    mark = "[OK]" if cond else "[FAIL]"
    if not cond: fail_count += 1
    print(f"  {mark} {name}   {detail}")

def sep(t): print("\n" + "=" * 78 + "\n  " + t + "\n" + "=" * 78)

# ============================================================================
sep("1. Data source config state")
# ============================================================================
rows, _ = run_q("""SELECT id, name, is_active, sync_status, last_sync_at FROM data_sources
                   WHERE cloud_account_id=3 ORDER BY id""")
for r in rows:
    print(f"  ds#{r[0]:<2} {r[1]:<20} is_active={r[2]}  status={r[3]:<8}  last={r[4]}")
# ds#3,4,5,6 should be active; ds#7 should be inactive
active_map = {r[0]: r[2] for r in rows}
check("ds#3 active", active_map.get(3) is True)
check("ds#4 active", active_map.get(4) is True)
check("ds#5 active", active_map.get(5) is True)
check("ds#6 active", active_map.get(6) is True)
check("ds#7 INACTIVE (historical backfill only)", active_map.get(7) is False)

# ============================================================================
sep("2. No hash / NULL project_id left anywhere (GCP)")
# ============================================================================
rows, _ = run_q("""SELECT data_source_id, COUNT(*) FROM billing_data
                   WHERE provider='gcp' AND (project_id IS NULL OR project_id ~ '^project-[0-9a-f]{12}$')
                   GROUP BY data_source_id""")
if not rows:
    check("No hash/NULL project_id in any GCP ds", True)
else:
    for r in rows: print(f"    ds={r[0]} still has {r[1]} dirty rows")
    check("No hash/NULL project_id in any GCP ds", False)

# ============================================================================
sep("3. Unique constraint integrity (no duplicate keys)")
# ============================================================================
rows, _ = run_q("""SELECT date, data_source_id, project_id, product, usage_type, region, COUNT(*)
                   FROM billing_data
                   GROUP BY date, data_source_id, project_id, product, usage_type, region
                   HAVING COUNT(*) > 1 LIMIT 5""")
check("No duplicate unique-key rows", len(rows) == 0,
      f"(first violations: {rows[:3]})" if rows else "")

# NULL in key columns that would bypass constraint
rows, _ = run_q("""SELECT COUNT(*) FROM billing_data
                   WHERE project_id IS NULL OR product IS NULL OR usage_type IS NULL OR region IS NULL""")
null_key_rows = rows[0][0]
check("No NULL in dedup-key columns", null_key_rows == 0, f"(found {null_key_rows})")

# ============================================================================
sep("4. billing_daily_summary vs billing_data (per-ds per-month)")
# ============================================================================
rows, _ = run_q("""
    WITH raw AS (SELECT data_source_id, TO_CHAR(DATE_TRUNC('month',date),'YYYY-MM') mon,
                        ROUND(SUM(cost)::numeric,2) c
                 FROM billing_data WHERE provider='gcp' AND date >= '2025-10-01'
                 GROUP BY 1,2),
         sum AS (SELECT data_source_id, TO_CHAR(DATE_TRUNC('month',date),'YYYY-MM') mon,
                        ROUND(SUM(total_cost)::numeric,2) c
                 FROM billing_daily_summary WHERE provider='gcp' AND date >= '2025-10-01'
                 GROUP BY 1,2)
    SELECT COALESCE(r.data_source_id,s.data_source_id) ds,
           COALESCE(r.mon,s.mon) mon,
           r.c raw_cost, s.c sum_cost,
           ABS(COALESCE(r.c,0)-COALESCE(s.c,0)) diff
    FROM raw r FULL OUTER JOIN sum s USING (data_source_id, mon)
    WHERE ABS(COALESCE(r.c,0) - COALESCE(s.c,0)) >= 0.01
    ORDER BY ds, mon""")
check("billing_daily_summary matches billing_data for all (gcp ds, month)", len(rows) == 0)
if rows:
    print("    drifts found:")
    for r in rows[:10]: print(f"    ds={r[0]} {r[1]} raw=${r[2]} sum=${r[3]} diff=${r[4]}")

# ============================================================================
sep("5. ds#7 coverage alignment: DB vs BQ native 01186D per-month")
# ============================================================================
q = """SELECT FORMAT_DATE('%Y-%m', DATE(usage_start_time)) mon,
              ROUND(SUM(cost), 2) c, COUNT(*) n
       FROM `xmagnet.spaceone_billing_data_us.gcp_billing_export_v1_01186D_EC0E18_F83B2B`
       WHERE DATE(usage_start_time) BETWEEN '2025-10-01' AND '2026-03-31'
       GROUP BY mon ORDER BY mon"""
bq_native = {r.mon: (Decimal(str(r.c or 0)), r.n) for r in bq.query(q).result()}

rows, _ = run_q("""SELECT TO_CHAR(DATE_TRUNC('month',date),'YYYY-MM') mon,
                          ROUND(SUM(cost)::numeric,2), COUNT(*)
                   FROM billing_data WHERE data_source_id=7
                   GROUP BY 1 ORDER BY 1""")
db_ds7 = {r[0]: (Decimal(str(r[1])), r[2]) for r in rows}

print(f"  {'month':<8} {'BQ cost':>13} {'DB cost':>13} {'diff':>10}  {'BQ rows':>8} {'DB rows':>8}")
all_diff = Decimal("0")
for m in sorted(set(bq_native) | set(db_ds7)):
    bc, bn = bq_native.get(m, (Decimal("0"), 0))
    dc, dn = db_ds7.get(m, (Decimal("0"), 0))
    diff = dc - bc
    all_diff += abs(diff)
    flag = " ← OFF" if abs(diff) >= Decimal("1.00") else ""
    print(f"  {m:<8} {float(bc):>13,.2f} {float(dc):>13,.2f} {float(diff):>10,.2f}  {bn:>8,} {dn:>8,}{flag}")
check("ds#7 BQ-DB monthly alignment within $10", all_diff < Decimal("10"))

# ============================================================================
sep("6. ds#3-6 coverage alignment: DB vs BQ VIEW per-month (Apr 1-22)")
# ============================================================================
view_map = {
    3: ("share-service-nonprod.xmind.billing_report", "cost_at_list"),
    4: ("share-service-nonprod.testmanger.billing_report", "cost_at_list"),
    5: ("cb-export.other.xm", "cost"),
    6: ("px-billing-report.other.xm", "cost"),
}
for ds_id, (fqt, cost_field) in view_map.items():
    q = f"""SELECT ROUND(SUM({cost_field}), 2) c, COUNT(*) n
            FROM `{fqt}` WHERE DATE(usage_start_time) BETWEEN '2026-04-01' AND '2026-04-22'"""
    r = list(bq.query(q).result())[0]
    bq_c = Decimal(str(r.c or 0)); bq_n = r.n
    rows, _ = run_q(f"""SELECT ROUND(SUM(cost)::numeric,2), COUNT(*)
                        FROM billing_data WHERE data_source_id={ds_id}
                        AND date BETWEEN '2026-04-01' AND '2026-04-22'""")
    db_c = Decimal(str(rows[0][0] or 0)); db_n = rows[0][1]
    diff = db_c - bq_c
    # Tolerance: DB usually >= BQ by up to a few % due to aggregation + late-day BQ reshuffle
    print(f"  ds#{ds_id}  BQ=${float(bq_c):>12,.2f} ({bq_n:>8,})  DB=${float(db_c):>12,.2f} ({db_n:>8,})  diff=${float(diff):>10,.2f}")
    # For ds=5/6, BQ raw count > DB because collector does GROUP BY (date, project, service, sku, region) + SUM
    # So DB rows << BQ rows is expected. Cost should match within a small tolerance.
    check(f"ds#{ds_id} Apr-1~22 cost matches BQ within 5%",
          (bq_c == 0) or (abs(diff) / max(bq_c, Decimal("0.01")) < Decimal("0.05")),
          f"(BQ ${bq_c}, DB ${db_c})")

# ============================================================================
sep("7. Historical: 2025-11 & 2025-12 restoration sanity")
# ============================================================================
# These were the most-impaired months; verify they're now close to BQ truth
for mon, expected_range in [("2025-11", (33000, 34500)), ("2025-12", (71000, 73500))]:
    rows, _ = run_q(f"""SELECT ROUND(SUM(cost)::numeric,2) FROM billing_data
                        WHERE provider='gcp' AND TO_CHAR(DATE_TRUNC('month',date),'YYYY-MM')='{mon}'""")
    v = Decimal(str(rows[0][0] or 0))
    ok = Decimal(str(expected_range[0])) <= v <= Decimal(str(expected_range[1]))
    check(f"{mon} total in expected range ${expected_range[0]:,}-${expected_range[1]:,}",
          ok, f"(got ${v})")

# ============================================================================
sep("8. Overall GCP total sanity")
# ============================================================================
rows, _ = run_q("SELECT ROUND(SUM(cost)::numeric,2) FROM billing_data WHERE provider='gcp'")
total = Decimal(str(rows[0][0]))
check(f"GCP total in expected range $1.70M - $1.72M",
      Decimal("1700000") < total < Decimal("1720000"),
      f"(got ${total:,.2f})")

# ============================================================================
sep("9. Sync logs recent success (no regressions)")
# ============================================================================
rows, _ = run_q("""SELECT data_source_id, COUNT(*) total,
                          SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) succ,
                          SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) fail
                   FROM sync_logs WHERE data_source_id IN (3,4,5,6,7) AND start_time >= NOW() - INTERVAL '7 days'
                   GROUP BY data_source_id ORDER BY data_source_id""")
for r in rows:
    print(f"  ds#{r[0]}  last 7d: total={r[1]}  success={r[2]}  failed={r[3]}")

# ============================================================================
sep("10. Final summary")
# ============================================================================
print(f"\n  Total checks failed: {fail_count}")
if fail_count == 0:
    print("  ALL GREEN - data is complete and aligned")
else:
    print(f"  {fail_count} issues flagged above")
