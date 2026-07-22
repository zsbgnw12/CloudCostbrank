# -*- coding: utf-8 -*-
"""全量重建 billing_daily_summary(含双币 total_cost_usd/cny),消除历史陈旧/缺行。

口径与 app/services/sync_service.refresh_daily_summary 完全一致,只是覆盖全部日期。
单事务 TRUNCATE + INSERT,失败自动回滚(不会留下空表)。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _db import connect  # noqa: E402


def main():
    c = connect(readonly=False)
    cur = c.cursor()
    try:
        cur.execute("SELECT ROUND(SUM(cost)::numeric,2) FROM billing_summary")
        detail = cur.fetchone()[0]

        cur.execute("TRUNCATE billing_daily_summary RESTART IDENTITY")
        cur.execute("""
            INSERT INTO billing_daily_summary
                (date, provider, data_source_id, project_id, product,
                 total_cost, total_cost_usd, total_cost_cny,
                 total_cost_at_list, total_credits,
                 total_usage, record_count)
            SELECT
                date, provider, data_source_id, project_id, product,
                SUM(cost),
                SUM(COALESCE(cost_usd, cost)),
                SUM(cost_cny),
                SUM(cost_at_list),
                SUM(credits_total),
                SUM(usage_quantity),
                COUNT(*)
            FROM billing_summary
            GROUP BY date, provider, data_source_id, project_id, product
        """)
        cur.execute("SELECT ROUND(SUM(total_cost)::numeric,2), ROUND(SUM(total_cost_usd)::numeric,2), COUNT(*) FROM billing_daily_summary")
        agg_cost, agg_usd, rows = cur.fetchone()
        c.commit()
        print(f"重建完成:{rows} 行")
        print(f"  明细 SUM(cost)      = {detail}")
        print(f"  预聚合 SUM(total_cost) = {agg_cost}  (差 {round(float(detail)-float(agg_cost),2)})")
        print(f"  预聚合 SUM(total_cost_usd) = {agg_usd}")
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


if __name__ == "__main__":
    main()
