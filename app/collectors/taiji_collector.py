"""Taiji (New-API fork) billing + token-usage collector.

Taiji 是基于 New-API 二次开发的内部 AI 聚合平台。日志接口沿用 New-API 的
 `/api/log/` 规范：分页、按时间戳过滤、按 `type=2` 取消费日志。

Secret data schema (Fernet-encrypted in CloudAccount):
    {
        "api_base": "https://api.taijiaicloud.com",
        "access_token": "<admin access token>",   # Authorization: Bearer <token>
        "admin_user_id": "1"                       # 可选：New-API-User header，大多数部署需要
    }

DataSource.config schema:
    {
        "quota_per_usd": 500000,        # quota 换算美元倍数，OneAPI/New-API 默认 500000
        "filter_username": null,        # 可选：只拉某个用户
        "filter_token_name": null,      # 可选：只拉某个 token
        "page_size": 100,               # 可选：分页大小
        "page_start": 1                 # 可选：首页的 p 值；多数 new-api 版本是 1-based，
                                        # 新 fork 若为 0-based 可改成 0
    }

返回 billing rows 兼容 sync_service.upsert_billing_rows 的格式，并在每行
附带 `_token_usage` 字段（dict），供 sync_service 侧再做一次按
(date, model) 的聚合 upsert 进 token_usage 表。
"""

import datetime as dt
import logging
from collections import defaultdict
from typing import Any

import httpx

from app.collectors.base import BaseCollector

logger = logging.getLogger(__name__)

# 消费日志类型（New-API 约定：2 = consume / request）
_LOG_TYPE_CONSUME = 2

# 默认 quota → USD 转换倍数（OneAPI / New-API 惯例）
_DEFAULT_QUOTA_PER_USD = 500_000

_DEFAULT_PAGE_SIZE = 100


class TaijiCollector(BaseCollector):
    """从 Taiji (New-API) 拉请求日志，按天 × token × model 聚合成 billing rows。"""

    def collect_billing(
        self,
        secret_data: dict,
        config: dict,
        start_date: str,  # YYYY-MM-DD
        end_date: str,    # YYYY-MM-DD
    ) -> list[dict]:
        api_base = (secret_data.get("api_base") or "").rstrip("/")
        access_token = secret_data.get("access_token")
        admin_user_id = secret_data.get("admin_user_id")
        if not api_base or not access_token:
            raise ValueError("taiji secret_data 缺少 api_base 或 access_token")

        quota_per_usd = int(config.get("quota_per_usd") or _DEFAULT_QUOTA_PER_USD)
        page_size = int(config.get("page_size") or _DEFAULT_PAGE_SIZE)
        # new-api 多数版本的 /api/log/ 首页 p=1（handler 内 offset=(p-1)*size）。
        # 若目标部署是新 fork 的 0-based 风格，改成 config.page_start=0。
        page_start = int(config.get("page_start") if config.get("page_start") is not None else 1)
        filter_username = config.get("filter_username")
        filter_token_name = config.get("filter_token_name")

        start_ts, end_ts = _date_range_to_unix(start_date, end_date)

        logger.info(
            "Taiji fetch logs: base=%s [%s~%s] ts=[%d~%d) page_size=%d",
            api_base, start_date, end_date, start_ts, end_ts, page_size,
        )

        raw_logs = self._fetch_all_logs(
            api_base=api_base,
            access_token=access_token,
            admin_user_id=admin_user_id,
            start_ts=start_ts,
            end_ts=end_ts,
            page_size=page_size,
            page_start=page_start,
            filter_username=filter_username,
            filter_token_name=filter_token_name,
        )

        logger.info("Taiji received %d raw log records", len(raw_logs))
        return _aggregate_logs(raw_logs, quota_per_usd=quota_per_usd)

    def collect_resources(self, secret_data: dict, config: dict) -> list[dict]:
        return []

    # ────────────────────────── HTTP ──────────────────────────

    @staticmethod
    def _fetch_all_logs(
        *,
        api_base: str,
        access_token: str,
        admin_user_id: str | None,
        start_ts: int,
        end_ts: int,
        page_size: int,
        page_start: int,
        filter_username: str | None,
        filter_token_name: str | None,
    ) -> list[dict]:
        """New-API 的 /api/log/ 分页拉取。首页 p 由 config.page_start 决定，默认 1。"""
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        if admin_user_id:
            # New-API 多部署要求带此 header 才能拿到全站日志
            headers["New-API-User"] = str(admin_user_id)

        base_params: dict[str, Any] = {
            "type": _LOG_TYPE_CONSUME,
            "start_timestamp": start_ts,
            "end_timestamp": end_ts,
            "page_size": page_size,
        }
        if filter_username:
            base_params["username"] = filter_username
        if filter_token_name:
            base_params["token_name"] = filter_token_name

        url = f"{api_base}/api/log/"

        all_items: list[dict] = []
        page = page_start
        total_hint: int | None = None
        with httpx.Client(timeout=httpx.Timeout(30.0, read=60.0)) as client:
            while True:
                params = {**base_params, "p": page}
                resp = client.get(url, headers=headers, params=params)
                resp.raise_for_status()
                payload = resp.json()

                if not payload.get("success", True):
                    raise RuntimeError(f"Taiji /api/log/ returned error: {payload.get('message')}")

                data = payload.get("data")
                # 形态 A: {success, data: {items: [...], total: N}}
                # 形态 B: {success, data: [...], total: N}
                if isinstance(data, dict):
                    items = data.get("items") or []
                    if total_hint is None:
                        total_hint = data.get("total")
                elif isinstance(data, list):
                    items = data
                    if total_hint is None:
                        total_hint = payload.get("total")
                else:
                    items = []

                if not items:
                    break

                all_items.extend(items)

                if len(items) < page_size:
                    break
                if total_hint is not None and len(all_items) >= total_hint:
                    break

                page += 1
                # 防御：避免坏掉的接口让我们无限翻页
                if page > 20000:
                    logger.warning("Taiji pagination hit safety cap at page %d", page)
                    break

        return all_items


# ────────────────────── 辅助：聚合 ──────────────────────

def _date_range_to_unix(start_date: str, end_date: str) -> tuple[int, int]:
    """将 [start_date, end_date] 转成 unix 秒区间 [start, end+1d)。"""
    sd = dt.date.fromisoformat(start_date)
    ed = dt.date.fromisoformat(end_date) + dt.timedelta(days=1)
    # 注意：taiji 的 created_at 通常是 UTC unix 秒；线上若走北京时区可调此处
    start_ts = int(dt.datetime(sd.year, sd.month, sd.day, tzinfo=dt.timezone.utc).timestamp())
    end_ts = int(dt.datetime(ed.year, ed.month, ed.day, tzinfo=dt.timezone.utc).timestamp())
    return start_ts, end_ts


def _aggregate_logs(raw_logs: list[dict], *, quota_per_usd: int) -> list[dict]:
    """
    将 taiji 原始请求日志按 (date, token_id, model_name, channel_name) 聚合成 billing row。

    同时为每行附带 `_token_usage` 子字典（sync_service 会据此再做一次 token_usage 表聚合）。
    """
    # (date, token_id, token_name, username, model, channel) → 累加器
    bucket: dict[tuple, dict] = defaultdict(lambda: {
        "quota_sum": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cache_tokens": 0,
        "request_count": 0,
        "total_use_time_ms": 0,
    })

    for log in raw_logs:
        created_at = log.get("created_at") or 0
        if not created_at:
            continue
        date = dt.datetime.fromtimestamp(int(created_at), tz=dt.timezone.utc).date().isoformat()

        token_id = log.get("token_id") or 0
        token_name = log.get("token_name") or ""
        username = log.get("username") or ""
        model_name = log.get("model_name") or "unknown"
        channel_name = log.get("channel_name") or _guess_channel_from_other(log.get("other"))

        key = (date, int(token_id), token_name, username, model_name, channel_name or "")
        acc = bucket[key]
        acc["quota_sum"] += int(log.get("quota") or 0)
        acc["prompt_tokens"] += int(log.get("prompt_tokens") or 0)
        acc["completion_tokens"] += int(log.get("completion_tokens") or 0)
        acc["request_count"] += 1
        acc["total_use_time_ms"] += int(log.get("use_time") or 0)
        # cache 存在 other JSON 里
        cache = _extract_cache_tokens(log.get("other"))
        acc["cache_tokens"] += cache

    rows: list[dict] = []
    for (date, token_id, token_name, username, model_name, channel), acc in bucket.items():
        cost_usd = acc["quota_sum"] / quota_per_usd if quota_per_usd > 0 else 0.0
        total_tokens = acc["prompt_tokens"] + acc["completion_tokens"]
        project_id = str(token_id)
        project_name = _render_project_name(username, token_name, token_id)

        row = {
            "date": date,
            "project_id": project_id,
            "project_name": project_name,
            "product": model_name,
            "usage_type": channel or "",
            "region": channel or None,
            "cost": round(cost_usd, 6),
            "usage_quantity": float(total_tokens),
            "usage_unit": "tokens",
            "currency": "USD",
            "tags": {},
            "additional_info": {
                "taiji_username": username,
                "taiji_token_name": token_name,
                "taiji_token_id": token_id,
                "taiji_channel": channel,
                "request_count": acc["request_count"],
                "prompt_tokens": acc["prompt_tokens"],
                "completion_tokens": acc["completion_tokens"],
                "cache_tokens": acc["cache_tokens"],
                "avg_use_time_ms": (
                    acc["total_use_time_ms"] // acc["request_count"]
                    if acc["request_count"] else 0
                ),
            },
            # ↓ sync_service 会读取此字段再写 token_usage 表；不进 billing_data
            "_token_usage": {
                "date": date,
                "model_id": model_name,
                "model_name": model_name,
                "region": None,  # token_usage 按 (date, ds, model) 聚合，不按 channel 拆
                "request_count": acc["request_count"],
                "input_tokens": acc["prompt_tokens"],
                "output_tokens": acc["completion_tokens"],
                "cache_read_tokens": acc["cache_tokens"],
                "cache_write_tokens": 0,
                "total_tokens": total_tokens,
                "input_cost": 0.0,    # taiji 不区分拆分成本，总额记入 total_cost
                "output_cost": 0.0,
                "total_cost": round(cost_usd, 6),
                "currency": "USD",
            },
        }
        rows.append(row)

    return rows


def _render_project_name(username: str, token_name: str, token_id: int) -> str:
    """名字格式：'username:token_name'；兜底加 token_id 后缀防重名。"""
    u = (username or "").strip()
    tn = (token_name or "").strip()
    if u and tn:
        return f"{u}:{tn}"
    if tn:
        return f"{tn} (tok#{token_id})"
    if u:
        return f"{u} (tok#{token_id})"
    return f"token#{token_id}"


def _guess_channel_from_other(other_raw) -> str | None:
    """有些 new-api 部署 channel_name 为 null，channel id 在 other.admin_info.use_channel 里。"""
    parsed = _parse_other(other_raw)
    if not parsed:
        return None
    uc = parsed.get("admin_info", {}).get("use_channel") if isinstance(parsed.get("admin_info"), dict) else None
    if isinstance(uc, list) and uc:
        return f"ch#{uc[0]}"
    return None


def _extract_cache_tokens(other_raw) -> int:
    parsed = _parse_other(other_raw)
    if not parsed:
        return 0
    try:
        return int(parsed.get("cache_tokens") or 0)
    except (ValueError, TypeError):
        return 0


def _parse_other(other_raw) -> dict | None:
    """other 字段可能是字符串化 JSON，也可能已经是 dict。"""
    if not other_raw:
        return None
    if isinstance(other_raw, dict):
        return other_raw
    if isinstance(other_raw, str):
        import json as _json
        try:
            return _json.loads(other_raw)
        except (ValueError, TypeError):
            return None
    return None
