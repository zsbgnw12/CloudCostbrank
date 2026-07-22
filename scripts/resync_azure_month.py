# -*- coding: utf-8 -*-
"""同步(重跑)指定月份的所有 Azure 数据源,应用新采集逻辑(PublisherType / product 回退 / 双币固化)。

用途:修正历史 Azure 数据里 product 为空的行 + 补 Marketplace 发布商标签 + 固化 cost_usd/cost_cny。
覆盖式 upsert(ON CONFLICT DO UPDATE),不会产生重复;安全可重复运行。

必须在**配置了 .env 的后端环境**运行(需要 AES_SECRET_KEY 解密云凭据、SYNC_DATABASE_URL、
以及到 Azure Cost Management 的网络)。

用法:
    python scripts/resync_azure_month.py 2026-06          # 指定月
    python scripts/resync_azure_month.py                  # 默认上个月
"""
import calendar
import datetime as dt
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.sync_service import get_active_data_sources  # noqa: E402
from tasks.sync_tasks import _run_sync_core  # noqa: E402


def month_range(month: str) -> tuple[str, str]:
    y, m = map(int, month.split("-"))
    last = calendar.monthrange(y, m)[1]
    return f"{month}-01", f"{month}-{last:02d}"


def main():
    if len(sys.argv) >= 2:
        month = sys.argv[1]
    else:
        first = dt.date.today().replace(day=1)
        month = (first - dt.timedelta(days=1)).strftime("%Y-%m")

    start_date, end_date = month_range(month)
    sources = [s for s in get_active_data_sources() if s["provider"] == "azure"]
    print(f"重同步 Azure {month} ({start_date}~{end_date}),共 {len(sources)} 个数据源")

    ok = fail = 0
    for s in sources:
        dsid = s["data_source_id"]
        try:
            r = _run_sync_core(dsid, start_date, end_date)
            ok += 1
            print(f"  [OK] DS#{dsid} fetched={r['fetched']} upserted={r['upserted']}")
        except Exception as e:
            fail += 1
            print(f"  [FAIL] DS#{dsid}: {type(e).__name__}: {e}")

    print(f"完成:成功 {ok} / 失败 {fail} / 共 {len(sources)}")


if __name__ == "__main__":
    main()
