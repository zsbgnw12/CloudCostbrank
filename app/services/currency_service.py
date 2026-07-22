"""货币规范化:入库时把原始账单金额固化成 USD + CNY 双币。

设计(见 alembic 025 迁移说明):
- `cost` + `currency` 是原始账单币种金额,永不改写(审计/发票对账唯一真相)。
- `cost_usd` 是内部统一聚合口径;`cost_cny` 是给中国客户的冻结人民币额。
- 两者都在**入库时按当日汇率固化一次**,历史不随汇率变动。

换算优先级:哪个币种源头原生给了就用源头的数(最权威),缺的那个用当日 USD↔CNY 汇率换算一次。
Azure 的 `currency_conversion_rate` = ExchangeRatePricingToBilling(pricing USD → billing 本币),
所以 CNY 结算时 cost_usd = cost / rate。
"""

import datetime as dt
import logging
from decimal import Decimal, InvalidOperation

from sqlalchemy import text

logger = logging.getLogger(__name__)

# 兜底汇率:exchange_rates 表尚无当日数据时使用。仅为避免 ingest 崩溃,会记 warning。
# 正式数据由 scripts/backfill_exchange_rates.py 回填 + 每日 Celery 任务维护。
FALLBACK_USD_CNY = Decimal("7.20")

_ZERO = Decimal("0")


def _to_decimal(v) -> Decimal:
    if v is None or v == "":
        return _ZERO
    if isinstance(v, Decimal):
        return v
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        return _ZERO


def store_usd_cny_rate(conn, date, rate) -> None:
    """幂等写入一条 USD→CNY 汇率(存在则更新)。conn 为 SQLAlchemy Connection。"""
    conn.execute(
        text(
            "INSERT INTO exchange_rates (date, from_currency, to_currency, rate) "
            "VALUES (:d, 'USD', 'CNY', :r) "
            "ON CONFLICT (date, from_currency, to_currency) DO UPDATE SET rate = EXCLUDED.rate"
        ),
        {"d": date, "r": str(rate)},
    )


def fetch_usd_cny(date_iso: str) -> Decimal | None:
    """从 frankfurter.app(ECB 数据,免费无 key)取某日 USD→CNY 汇率。

    仅做只读 HTTP GET,不发送任何用户数据。失败返回 None(调用方兜底)。
    """
    import httpx

    url = f"https://api.frankfurter.app/{date_iso}"
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(url, params={"from": "USD", "to": "CNY"})
            resp.raise_for_status()
            data = resp.json()
        rate = (data.get("rates") or {}).get("CNY")
        return Decimal(str(rate)) if rate else None
    except Exception as e:
        logger.warning("fetch_usd_cny(%s) failed: %s", date_iso, e)
        return None


def load_usd_cny_rate_map(conn, start_date, end_date) -> dict[str, Decimal]:
    """一次性读取 [start,end] 区间的 USD→CNY 每日汇率,返回 {date_iso: rate}。"""
    rows = conn.execute(
        text(
            "SELECT date, rate FROM exchange_rates "
            "WHERE from_currency='USD' AND to_currency='CNY' "
            "AND date >= :s AND date <= :e"
        ),
        {"s": start_date, "e": end_date},
    ).fetchall()
    out: dict[str, Decimal] = {}
    for d, r in rows:
        key = d.isoformat() if hasattr(d, "isoformat") else str(d)[:10]
        out[key] = _to_decimal(r)
    return out


def latest_usd_cny_rate(conn, on_or_before=None) -> Decimal | None:
    """取最近一条 USD→CNY 汇率(可选 on_or_before 日期),表空返回 None。"""
    q = "SELECT rate FROM exchange_rates WHERE from_currency='USD' AND to_currency='CNY'"
    params: dict = {}
    if on_or_before is not None:
        q += " AND date <= :d"
        params["d"] = on_or_before
    q += " ORDER BY date DESC LIMIT 1"
    row = conn.execute(text(q), params).fetchone()
    return _to_decimal(row[0]) if row else None


def resolve_rate(date_iso: str, rate_map: dict[str, Decimal], fallback: Decimal) -> Decimal:
    """给定日期取 USD→CNY:精确当日 → 最近的更早一天 → fallback。"""
    r = rate_map.get(date_iso)
    if r and r > 0:
        return r
    earlier = [k for k in rate_map if k <= date_iso]
    if earlier:
        r = rate_map[max(earlier)]
        if r and r > 0:
            return r
    return fallback


def compute_dual_currency(
    cost,
    currency: str | None,
    conversion_rate,
    usd_cny_rate: Decimal,
) -> tuple[Decimal, Decimal]:
    """把一行的原始 cost 固化成 (cost_usd, cost_cny)。

    cost            原始账单币种金额
    currency        原始币种 (USD/CNY/...)
    conversion_rate provider 自带 pricing(USD)→billing 本币汇率(Azure 有;可能 None/1)
    usd_cny_rate    当日 USD→CNY(由 resolve_rate 得出,保证 > 0)
    """
    amount = _to_decimal(cost)
    cur = (currency or "USD").upper()
    rate = _to_decimal(conversion_rate)

    if cur == "USD":
        usd = amount
        cny = amount * usd_cny_rate
    elif cur == "CNY":
        cny = amount
        # rate = USD→CNY;有则用源头汇率反推 USD(最权威),否则用中央 USD→CNY 表
        usd = (amount / rate) if rate > 0 else (amount / usd_cny_rate if usd_cny_rate > 0 else amount)
    else:
        # 其他币种(EUR 等):pricing 通常是 USD,cost = USD × rate → USD = cost / rate
        if rate > 0:
            usd = amount / rate
        else:
            usd = amount
            logger.warning(
                "compute_dual_currency: 未知币种 %s 且无 conversion_rate,cost_usd 按原值兜底(可能失真)",
                cur,
            )
        cny = usd * usd_cny_rate

    return usd.quantize(Decimal("0.000001")), cny.quantize(Decimal("0.000001"))


def annotate_rows_with_dual_currency(conn, rows: list[dict]) -> None:
    """就地给一批 billing row 补 cost_usd / cost_cny 两个键。

    rows 里每行需含 date / cost / currency / currency_conversion_rate。
    date 允许 'YYYY-MM-DD' 字符串或 date 对象。
    """
    if not rows:
        return

    def _date_iso(v) -> str:
        if hasattr(v, "isoformat"):
            return v.isoformat()[:10]
        return str(v)[:10]

    date_isos = [_date_iso(r.get("date")) for r in rows if r.get("date")]
    if not date_isos:
        # 没有日期无法定位汇率,统一用最近一条 / fallback
        fallback = latest_usd_cny_rate(conn) or FALLBACK_USD_CNY
        for r in rows:
            usd, cny = compute_dual_currency(
                r.get("cost"), r.get("currency"), r.get("currency_conversion_rate"), fallback
            )
            r["cost_usd"], r["cost_cny"] = usd, cny
        return

    start, end = min(date_isos), max(date_isos)
    rate_map = load_usd_cny_rate_map(conn, start, end)
    if not rate_map:
        # 区间内无数据,取表里最近一条兜底,再没有就常量兜底
        latest = latest_usd_cny_rate(conn, on_or_before=end) or FALLBACK_USD_CNY
        logger.warning(
            "annotate_rows_with_dual_currency: exchange_rates 在 %s~%s 无 USD→CNY 数据,"
            "使用兜底 %s(请尽快回填汇率表)",
            start, end, latest,
        )
        rate_map = {}  # 让 resolve_rate 走 fallback
        fallback = latest
    else:
        fallback = FALLBACK_USD_CNY

    for r in rows:
        d_iso = _date_iso(r.get("date")) if r.get("date") else end
        rate = resolve_rate(d_iso, rate_map, fallback)
        usd, cny = compute_dual_currency(
            r.get("cost"), r.get("currency"), r.get("currency_conversion_rate"), rate
        )
        r["cost_usd"], r["cost_cny"] = usd, cny
