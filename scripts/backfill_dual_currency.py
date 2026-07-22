# -*- coding: utf-8 -*-
"""回填存量 billing_summary 的 cost_usd / cost_cny,并刷新 billing_daily_summary 双币合计。

前置:先跑 scripts/backfill_exchange_rates.py 把 exchange_rates 填好。

策略(存量数据当前全部 currency='USD'):
  - cost_usd = cost
  - cost_cny = cost × 最近一条 <= 当日的 USD→CNY 汇率
非 USD 的存量行(理论上现在没有)用通用公式:
  - CNY 行:cost_cny = cost;cost_usd = cost / currency_conversion_rate
按月分批,避免长事务锁大表。新增数据的双币由 currency_service 在入库时固化,与此脚本无关。
"""
import sys
import os
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _db import connect  # noqa: E402


def month_iter(start: dt.date, end: dt.date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        first = dt.date(y, m, 1)
        nxt = dt.date(y + (1 if m == 12 else 0), (m % 12) + 1, 1)
        yield first, nxt
        y, m = nxt.year, nxt.month


def main():
    c = connect(readonly=False)
    cur = c.cursor()

    cur.execute("SELECT COUNT(*) FROM exchange_rates WHERE from_currency='USD' AND to_currency='CNY'")
    if cur.fetchone()[0] == 0:
        print("!! exchange_rates 无 USD->CNY 数据。请先运行 backfill_exchange_rates.py")
        c.close()
        return

    cur.execute("SELECT MIN(date), MAX(date) FROM billing_summary")
    mn, mx = cur.fetchone()
    if not mn:
        print("billing_summary 为空,无需回填")
        c.close()
        return

    total_usd = total_cny = 0
    for first, nxt in month_iter(mn, mx):
        # 1) USD / NULL 币种:cost_usd = cost
        cur.execute(
            "UPDATE billing_summary SET cost_usd = cost "
            "WHERE date >= %s AND date < %s AND cost_usd IS NULL "
            "AND (currency = 'USD' OR currency IS NULL)",
            (first, nxt),
        )
        # 2) 非 USD 币种:cost_usd = cost / currency_conversion_rate(rate=pricing USD->billing)
        cur.execute(
            "UPDATE billing_summary SET cost_usd = cost / NULLIF(currency_conversion_rate, 0) "
            "WHERE date >= %s AND date < %s AND cost_usd IS NULL "
            "AND currency IS NOT NULL AND currency <> 'USD' AND currency_conversion_rate > 0",
            (first, nxt),
        )
        u = cur.rowcount
        # 3) cost_cny:CNY 原币直接取 cost;其余用 cost_usd × 最近汇率
        cur.execute(
            "UPDATE billing_summary SET cost_cny = cost "
            "WHERE date >= %s AND date < %s AND cost_cny IS NULL AND currency = 'CNY'",
            (first, nxt),
        )
        cur.execute(
            "UPDATE billing_summary b SET cost_cny = b.cost_usd * er.rate "
            "FROM LATERAL ("
            "  SELECT rate FROM exchange_rates e "
            "  WHERE e.from_currency='USD' AND e.to_currency='CNY' AND e.date <= b.date "
            "  ORDER BY e.date DESC LIMIT 1"
            ") er "
            "WHERE b.date >= %s AND b.date < %s AND b.cost_cny IS NULL AND b.cost_usd IS NOT NULL",
            (first, nxt),
        )
        cc = cur.rowcount
        c.commit()
        total_cny += cc
        print(f"  {first:%Y-%m}: cost_usd~{u} rows, cost_cny={cc} rows")

    # 刷新预聚合表的双币合计(与 refresh_daily_summary 口径一致)
    print("Refreshing billing_daily_summary dual-currency totals ...")
    cur.execute("""
        UPDATE billing_daily_summary d SET
            total_cost_usd = s.usd,
            total_cost_cny = s.cny
        FROM (
            SELECT date, provider, data_source_id, project_id, product,
                   SUM(COALESCE(cost_usd, cost)) AS usd, SUM(cost_cny) AS cny
            FROM billing_summary
            GROUP BY date, provider, data_source_id, project_id, product
        ) s
        WHERE d.date = s.date AND d.provider = s.provider
          AND d.data_source_id = s.data_source_id
          AND d.project_id IS NOT DISTINCT FROM s.project_id
          AND d.product IS NOT DISTINCT FROM s.product
    """)
    c.commit()
    print(f"  daily_summary updated: {cur.rowcount} rows")
    c.close()
    print("DONE")


if __name__ == "__main__":
    main()
