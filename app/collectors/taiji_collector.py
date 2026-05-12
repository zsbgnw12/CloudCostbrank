"""Taiji billing collector — 双路径分发。

Taiji 是内部 AI 聚合平台(类 newapi 二次开发)。collector 按 secret_data 自动分发:

1) Blob 模式(推荐，对应 /accounts 新建 UX 的"粘贴快照 + Blob SAS URL"):
   secret_data = {"blob_sas_url": "https://<acc>.blob.core.windows.net/<container>?sp=r&...&sig=..."}
   按天 GET {date}_UTC+0.json，解析 taiji 顶层 section，对齐
   AWS account_id / GCP project.id 的角色:project_id = "<username>:<token_name>"。

2) 旧 API 模式(向后兼容):
   secret_data = {api_base, access_token, admin_user_id?}
   分页拉 /api/log/ 原始记录,聚合成 billing row。

DataSource.config (按模式不同):
    Blob 模式: {timezone_tag, filename_template, request_timeout_sec}
    API  模式: {quota_per_usd, filter_username, filter_token_name, page_size, page_start}
"""

import datetime as dt
import logging
from collections import defaultdict
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.collectors.base import BaseCollector

logger = logging.getLogger(__name__)

# 消费日志类型（New-API 约定：2 = consume / request）
_LOG_TYPE_CONSUME = 2

# 默认 quota → USD 转换倍数（OneAPI / New-API 惯例）
_DEFAULT_QUOTA_PER_USD = 500_000

_DEFAULT_PAGE_SIZE = 100

# Blob 模式默认配置
_DEFAULT_TIMEZONE_TAG = "UTC+0"
_DEFAULT_FILENAME_TEMPLATE = "{date}_{tz}.json"
_DEFAULT_REQUEST_TIMEOUT_SEC = 60.0


class TaijiCollector(BaseCollector):
    """Taiji billing 双路径采集器：按 secret_data 内容分发到 Blob 模式 / 旧 API 模式。"""

    def collect_billing(
        self,
        secret_data: dict,
        config: dict,
        start_date: str,  # YYYY-MM-DD
        end_date: str,    # YYYY-MM-DD
    ) -> list[dict]:
        sd = secret_data or {}
        # 优先 Blob 模式：只要有 blob_sas_url 就走这条；为空才回退 API 模式
        if (sd.get("blob_sas_url") or "").strip():
            return self._collect_via_blob(sd, config or {}, start_date, end_date)
        if (sd.get("api_base") or "").strip() and (sd.get("access_token") or "").strip():
            return self._collect_via_api(sd, config or {}, start_date, end_date)
        raise ValueError(
            "taiji secret_data 缺凭证：需要 blob_sas_url 或 api_base+access_token"
        )

    # ────────────────────────── Blob 模式 ──────────────────────────

    def _collect_via_blob(
        self, secret_data: dict, config: dict, start_date: str, end_date: str,
    ) -> list[dict]:
        """按天 GET {date}_UTC+0.json，解析 taiji section → billing rows。"""
        sas_url = secret_data["blob_sas_url"]
        tz_tag = config.get("timezone_tag") or _DEFAULT_TIMEZONE_TAG
        filename_template = config.get("filename_template") or _DEFAULT_FILENAME_TEMPLATE
        timeout_sec = float(config.get("request_timeout_sec") or _DEFAULT_REQUEST_TIMEOUT_SEC)

        d_start = dt.date.fromisoformat(start_date)
        d_end = dt.date.fromisoformat(end_date)
        if d_end < d_start:
            raise ValueError(f"end_date {end_date} 早于 start_date {start_date}")

        logger.info(
            "Taiji blob fetch: [%s ~ %s] tz=%s sas_host=%s",
            start_date, end_date, tz_tag, urlsplit(sas_url).netloc,
        )

        rows: list[dict] = []
        days_fetched = 0
        days_missing = 0
        with httpx.Client(timeout=httpx.Timeout(timeout_sec, read=timeout_sec)) as client:
            cur = d_start
            while cur <= d_end:
                filename = filename_template.format(date=cur.isoformat(), tz=tz_tag)
                url = _build_blob_url(sas_url, filename)
                resp = client.get(url)
                if resp.status_code == 404:
                    logger.info("Taiji blob missing (404): %s", filename)
                    days_missing += 1
                    cur += dt.timedelta(days=1)
                    continue
                resp.raise_for_status()
                day_rows = _parse_blob_day(resp.json(), default_date=cur.isoformat())
                rows.extend(day_rows)
                days_fetched += 1
                cur += dt.timedelta(days=1)
        logger.info(
            "Taiji blob done: %d rows / %d day(s) fetched, %d missing",
            len(rows), days_fetched, days_missing,
        )
        return rows

    # ────────────────────────── 旧 API 模式 ──────────────────────────

    def _collect_via_api(
        self, secret_data: dict, config: dict, start_date: str, end_date: str,
    ) -> list[dict]:
        api_base = (secret_data.get("api_base") or "").rstrip("/")
        access_token = secret_data.get("access_token")
        admin_user_id = secret_data.get("admin_user_id")

        quota_per_usd = int(config.get("quota_per_usd") or _DEFAULT_QUOTA_PER_USD)
        page_size = int(config.get("page_size") or _DEFAULT_PAGE_SIZE)
        page_start = int(config.get("page_start") if config.get("page_start") is not None else 1)
        filter_username = config.get("filter_username")
        filter_token_name = config.get("filter_token_name")

        start_ts, end_ts = _date_range_to_unix(start_date, end_date)
        logger.info(
            "Taiji API fetch: base=%s [%s~%s] ts=[%d~%d) page_size=%d",
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
        logger.info("Taiji API received %d raw records", len(raw_logs))
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


# ────────────────────── Blob 路径辅助 ──────────────────────

def _build_blob_url(container_sas_url: str, filename: str) -> str:
    """把 SAS 容器 URL 与单个文件名拼成完整 blob URL。

    输入容器 SAS:`https://acc.blob.core.windows.net/<container>?<query>`
    输出文件 URL:`https://acc.blob.core.windows.net/<container>/<filename>?<query>`

    Azure Blob 路径里的 `+` 必须 URL-encode 成 `%2B`（不 encode 服务端会当作
    空格解释，访问 `UTC+0.json` 时会变成找 `UTC 0.json` → 404）。Taiji 文件名
    形如 `2026-05-07_UTC+0.json`，必须保护。
    """
    # 保留 lstrip("/") 兼容上层显式带前导斜杠的传参；`+` 显式 encode
    safe_filename = filename.lstrip("/").replace("+", "%2B")
    parts = urlsplit(container_sas_url)
    new_path = parts.path.rstrip("/") + "/" + safe_filename
    return urlunsplit((parts.scheme, parts.netloc, new_path, parts.query, parts.fragment))


def _parse_blob_day(payload: dict, *, default_date: str) -> list[dict]:
    """把单天 blob JSON 展平成 billing rows。

    优先用 payload.date_range.start_at[:10] 作为 date,blob 文件名传入的
    default_date 仅做 fallback(一致性校验由调用方决定)。
    """
    date_str = default_date
    dr = payload.get("date_range") or {}
    if isinstance(dr, dict):
        start_at = dr.get("start_at") or ""
        if isinstance(start_at, str) and len(start_at) >= 10:
            date_str = start_at[:10]

    taiji_root = payload.get("taiji")
    if not isinstance(taiji_root, dict):
        return []

    rows: list[dict] = []
    for username, tokens in taiji_root.items():
        if not isinstance(tokens, dict):
            continue
        u = (username or "").strip() or "_"
        for token_name, token_blob in tokens.items():
            if not isinstance(token_blob, dict):
                continue
            tn = (token_name or "").strip() or "_"
            project_id = f"{u}:{tn}"
            key_display = token_blob.get("key_display") or ""
            details = token_blob.get("details") or {}
            if not isinstance(details, dict) or not details:
                continue

            for model_name, m in details.items():
                if not isinstance(m, dict):
                    continue
                cost = _to_float(m.get("cost"))
                prompt_tokens = _to_int(m.get("prompt_tokens"))
                completion_tokens = _to_int(m.get("completion_tokens"))
                count = _to_int(m.get("count"))
                cache_hit = _to_int(m.get("cache_hit_tokens"))
                total_tokens = prompt_tokens + completion_tokens

                additional = {
                    "key_display": key_display,
                    "request_count": count,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                }
                if cache_hit:
                    additional["cache_hit_tokens"] = cache_hit

                rows.append({
                    "date": date_str,
                    "project_id": project_id,
                    "project_name": project_id,
                    "product": model_name or "unknown",
                    "usage_type": "",
                    "region": None,
                    "cost_type": "regular",
                    "cost": round(cost, 6),
                    "usage_quantity": float(total_tokens),
                    "usage_unit": "tokens",
                    "currency": "USD",
                    "currency_conversion_rate": 1.0,
                    "tags": {},
                    "additional_info": additional,
                })
    return rows


def _to_float(v) -> float:
    if v is None:
        return 0.0
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0


def _to_int(v) -> int:
    if v is None:
        return 0
    try:
        return int(v)
    except (ValueError, TypeError):
        try:
            return int(float(v))
        except (ValueError, TypeError):
            return 0


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
            # billing_data.cost_type NOT NULL；taiji 全部按常规消费记账。
            "cost_type": "regular",
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
