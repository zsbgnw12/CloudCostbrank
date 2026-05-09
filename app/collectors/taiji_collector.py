"""Taiji billing collector — 每日 Azure Blob 预聚合 JSON 模式。

设计目标:输出行形态与 AWS / GCP / Azure 完全一致,只写 billing_summary,
不再写 token_usage 旁路 —— 平台上 metering / dashboard 等 UI 直接复用同一套
SQL,不需要 taiji 特判。Taiji 是 Push 数据的一方:数据生产方每天往
Azure Blob 容器里丢一个 `{YYYY-MM-DD}_UTC+0.json`,我们只负责按天拉下来落库。

Secret data schema (Fernet-encrypted in CloudAccount.secret_data):
    {
        "blob_sas_url": "https://<acc>.blob.core.windows.net/<container>?sp=r&...&sig=..."
    }
    SAS 必须是容器级(sr=c)只读(sp=r),包含完整 query string。

DataSource.config schema(均为可选):
    {
        "timezone_tag": "UTC+0",   # 文件名后缀,默认 UTC+0
        "filename_template": "{date}_{tz}.json",
        "request_timeout_sec": 60
    }

JSON 文件结构(按天预聚合):
    {
        "date_range": {"start_at": "2026-05-07 00:00:00", "end_at": "...", "timezone": "UTC+0"},
        "taiji": {
            "<username>": {
                "<token_name>": {
                    "key_display": "sk-XXX",
                    "total_cost": ...,
                    "total_count": ...,
                    "details": {
                        "<model>": {"prompt_tokens": .., "completion_tokens": .., "cost": .., "count": .., "cache_hit_tokens": ..}
                    }
                }
            }
        }
    }

落库映射(对齐 AWS account_id / GCP project.id / Azure subscription_id 的角色):
    project_id    = "<username>:<token_name>"   blob 内天然主键,等价 AWS account_id
    project_name  = same as project_id
    product       = <model>                      等价 AWS service / GCP service / Azure meterCategory
    usage_type    = ""                           taiji 暂无子分类
    region        = NULL                         taiji 不分 region
    cost / currency / currency_conversion_rate = details[model].cost / "USD" / 1.0
    cost_type     = "regular"
    usage_quantity / usage_unit = prompt+completion tokens / "tokens"
    additional_info = {key_display, request_count, prompt_tokens, completion_tokens, cache_hit_tokens?}

Push 路径(/api/metering/taiji/ingest → billing_raw_taiji)是历史遗留,与本采集器
互不依赖;保留 `_aggregate_logs` 等辅助函数纯粹是为了不打破老的 push ingest。
"""

import datetime as dt
import logging
from collections import defaultdict
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.collectors.base import BaseCollector

logger = logging.getLogger(__name__)

# Push 模式保留:quota → USD 兑换倍数(New-API 惯例)
_DEFAULT_QUOTA_PER_USD = 500_000
_DEFAULT_PAGE_SIZE = 100

_DEFAULT_TIMEZONE_TAG = "UTC+0"
_DEFAULT_FILENAME_TEMPLATE = "{date}_{tz}.json"
_DEFAULT_REQUEST_TIMEOUT_SEC = 60.0


class TaijiCollector(BaseCollector):
    """从 Azure Blob 拉每日预聚合 JSON,按 (date, token, model) 展平成 billing rows。"""

    def collect_billing(
        self,
        secret_data: dict,
        config: dict,
        start_date: str,  # YYYY-MM-DD
        end_date: str,    # YYYY-MM-DD
    ) -> list[dict]:
        sas_url = (secret_data or {}).get("blob_sas_url")
        if not sas_url:
            raise ValueError("taiji secret_data 缺少 blob_sas_url")

        config = config or {}
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
                    # 当天 blob 还没生成 / 未来日期,跳过即可
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
            "Taiji blob done: %d row(s) from %d day(s); %d day(s) missing",
            len(rows), days_fetched, days_missing,
        )
        return rows

    def collect_resources(self, secret_data: dict, config: dict) -> list[dict]:
        return []


# ────────────────────── URL 拼接 ──────────────────────

def _build_blob_url(container_sas_url: str, filename: str) -> str:
    """把 SAS 容器 URL 与单个文件名拼成完整 blob URL。

    输入容器 SAS:`https://acc.blob.core.windows.net/<container>?<query>`
    输出文件 URL:`https://acc.blob.core.windows.net/<container>/<filename>?<query>`

    `+` 在 path 中字面有效,无需特别处理;但若 filename 含 `?` / `#` 等则要 encode。
    Taiji 的命名只有 `[0-9-]_UTC+0.json` 这种可控形式,直接拼即可。
    """
    parts = urlsplit(container_sas_url)
    new_path = parts.path.rstrip("/") + "/" + filename.lstrip("/")
    return urlunsplit((parts.scheme, parts.netloc, new_path, parts.query, parts.fragment))


# ────────────────────── JSON 解析 ──────────────────────

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
                # token 当天 0 调用,details 为空。不入库 —— billing_summary 只关心
                # 有费用/用量的明细;空 token 仍可通过 token_usage / 项目自动发现去捕捉
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


# ────────────────────── Push 路径辅助(保留给 /api/metering/taiji/ingest) ──────────────────────
#
# 下面的 _date_range_to_unix / _aggregate_logs 等给 sync_service.reaggregate_from_taiji_raw
# 用,从 billing_raw_taiji 重算 billing_summary + token_usage。Pull 路径(blob)不再依赖,
# 但保留以免破坏 Push ingest 流程。

def _date_range_to_unix(start_date: str, end_date: str) -> tuple[int, int]:
    """[start, end] 闭区间日期 → unix 秒区间 [start, end+1d)。"""
    sd = dt.date.fromisoformat(start_date)
    ed = dt.date.fromisoformat(end_date) + dt.timedelta(days=1)
    start_ts = int(dt.datetime(sd.year, sd.month, sd.day, tzinfo=dt.timezone.utc).timestamp())
    end_ts = int(dt.datetime(ed.year, ed.month, ed.day, tzinfo=dt.timezone.utc).timestamp())
    return start_ts, end_ts


def _aggregate_logs(raw_logs: list[dict], *, quota_per_usd: int) -> list[dict]:
    """
    将 taiji 原始请求日志(billing_raw_taiji 中的行)按
    (date, token_id, token_name, username, model, channel) 聚合成 billing row。

    Push 模式下仍然按 token_id 作为 project_id(老约定保留),与 Pull 模式
    的 "username:token_name" 不一定互通 —— 同一 cloud_account 不应同时启用两条
    路径(seed 脚本里 is_active 默认 False 强制走 Push,Pull 启用前请确认 Push 不再供数)。
    """
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
            "_token_usage": {
                "date": date,
                "model_id": model_name,
                "model_name": model_name,
                "region": None,
                "request_count": acc["request_count"],
                "input_tokens": acc["prompt_tokens"],
                "output_tokens": acc["completion_tokens"],
                "cache_read_tokens": acc["cache_tokens"],
                "cache_write_tokens": 0,
                "total_tokens": total_tokens,
                "input_cost": 0.0,
                "output_cost": 0.0,
                "total_cost": round(cost_usd, 6),
                "currency": "USD",
            },
        }
        rows.append(row)

    return rows


def _render_project_name(username: str, token_name: str, token_id: int) -> str:
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
