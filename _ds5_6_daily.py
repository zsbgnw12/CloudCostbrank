import sys, json
sys.path.insert(0, '.')
from _db import run_q
from decimal import Decimal
from google.cloud import bigquery
from google.oauth2 import service_account

creds = service_account.Credentials.from_service_account_info(
    json.load(open('xmagnet-c0e170e58dc3.json')),
    scopes=['https://www.googleapis.com/auth/cloud-platform'])
bq = bigquery.Client(credentials=creds, project=creds.project_id)

for ds, fqt in [(5, 'cb-export.other.xm'), (6, 'px-billing-report.other.xm')]:
    print(f"\n=== ds#{ds} DB vs BQ {fqt} by DATE (Apr 1-22) ===")
    rows, _ = run_q(f"""SELECT date, ROUND(SUM(cost)::numeric,2), COUNT(*)
                        FROM billing_data WHERE data_source_id={ds}
                        AND date BETWEEN '2026-04-01' AND '2026-04-22'
                        GROUP BY date ORDER BY date""")
    db_map = {str(r[0]): (Decimal(str(r[1])), r[2]) for r in rows}

    q = f"""SELECT DATE(usage_start_time) d, ROUND(SUM(cost), 2) c, COUNT(*) n
            FROM `{fqt}`
            WHERE DATE(usage_start_time) BETWEEN '2026-04-01' AND '2026-04-22'
            GROUP BY d ORDER BY d"""
    bq_map = {str(r.d): (Decimal(str(r.c or 0)), r.n) for r in bq.query(q).result()}

    print(f"  {'date':<12} {'DB cost':>12} {'BQ cost':>12} {'diff':>10}")
    tot = Decimal('0')
    for d in sorted(set(db_map) | set(bq_map)):
        dc, _ = db_map.get(d, (Decimal('0'), 0))
        bc, _ = bq_map.get(d, (Decimal('0'), 0))
        diff = dc - bc
        tot += diff
        flag = ' OFF' if abs(diff) >= Decimal('0.50') else ''
        print(f"  {d:<12} {float(dc):>12,.2f} {float(bc):>12,.2f} {float(diff):>10,.2f}{flag}")
    print(f"  TOTAL diff = ${float(tot):,.2f}")
