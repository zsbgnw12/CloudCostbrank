# -*- coding: utf-8 -*-
"""回填历史 USD→CNY 每日汇率到 exchange_rates 表(frankfurter.app / ECB 数据)。

用法:
    python scripts/backfill_exchange_rates.py               # 默认覆盖 billing 最早日期~今天
    python scripts/backfill_exchange_rates.py 2025-09-01 2026-07-22

ECB 只在工作日发布,周末/节假日缺口由消费端(currency_service.resolve_rate)
取"最近的更早一天"兜底,无需在此逐日补齐。
"""
import sys
import os
import datetime as dt
from decimal import Decimal

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _db import connect  # noqa: E402


def fetch_range(start_iso: str, end_iso: str) -> dict[str, Decimal]:
    """frankfurter range 端点一次返回区间内所有工作日的 USD→CNY。"""
    url = f"https://api.frankfurter.app/{start_iso}..{end_iso}"
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        resp = client.get(url, params={"from": "USD", "to": "CNY"})
        resp.raise_for_status()
        data = resp.json()
    out: dict[str, Decimal] = {}
    for d, r in (data.get("rates") or {}).items():
        cny = r.get("CNY")
        if cny:
            out[d] = Decimal(str(cny))
    return out


def main():
    c = connect(readonly=False)
    cur = c.cursor()

    if len(sys.argv) >= 3:
        start_iso, end_iso = sys.argv[1], sys.argv[2]
    else:
        cur.execute("SELECT MIN(date), MAX(date) FROM billing_summary")
        mn, mx = cur.fetchone()
        start_iso = (mn or dt.date.today()).isoformat()
        end_iso = dt.date.today().isoformat()

    print(f"Fetching USD->CNY {start_iso} ~ {end_iso} ...")
    rates = fetch_range(start_iso, end_iso)
    print(f"  got {len(rates)} business-day rates")

    n = 0
    for d, rate in sorted(rates.items()):
        cur.execute(
            "INSERT INTO exchange_rates (date, from_currency, to_currency, rate) "
            "VALUES (%s,'USD','CNY',%s) "
            "ON CONFLICT (date, from_currency, to_currency) DO UPDATE SET rate=EXCLUDED.rate",
            (d, str(rate)),
        )
        n += 1
    c.commit()
    print(f"  upserted {n} rows into exchange_rates")
    c.close()


if __name__ == "__main__":
    main()
