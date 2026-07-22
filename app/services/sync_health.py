"""同步健康:把 sync_logs 的原始报错归类成人话 + 修复建议,并按货源汇总状态。

供 GET /api/sync/health 使用,前端据此渲染「顶部异常汇总条 + 货源健康列表」。
纯读,不改任何同步逻辑。
"""

import datetime as dt
import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# 状态阈值
STALE_HOURS = 36          # 距上次成功超过此值 → 数据滞后(即使没报错也提醒)
RUNNING_STALE_HOURS = 3   # running 超过此值 → 疑似卡死

# 错误分类字典:按优先级从上到下匹配 error_message(大小写不敏感)。
# 每项:(category, 正则/子串列表, 人话标题, 修复建议)
_RULES = [
    ("permission",
     [r"authorizationfailed", r"does not have authorization", r"authorization failed",
      r"does not have permission", r"forbidden"],
     "权限不足 / 被回收",
     "服务主体缺少该订阅的 Cost Management Reader 角色。请在对应 Azure 订阅「访问控制 (IAM)」重新授予。"),
    ("credential",
     [r"invalid_client", r"aadsts", r"unauthorized_client", r"invalid client secret",
      r"invalidauthenticationtoken", r"client secret", r"expired", r"token.*expired",
      r"signature", r"could not deserialize key", r"fernet", r"invalid.*credential",
      r"invalidclienttokenid", r"security token.*invalid", r"the security token",
      r"signaturedoesnotmatch", r"expiredtoken", r"accessdenied", r"unrecognizedclientexception"],
     "云账号凭据失效 / 过期",
     "云账号密钥或证书已失效/过期。请在「云账号」里更新该账号的凭据。"),
    ("throttled",
     [r"\b429\b", r"toomanyrequests", r"throttl", r"rate limit", r"quota exceeded"],
     "被云厂商限流",
     "触发云厂商 API 限流,系统会自动重试。通常无需处理,持续出现可放宽同步频率。"),
    ("not_found",
     [r"\b404\b", r"\b412\b", r"notfound", r"subscriptionnotfound", r"was not found",
      r"no billing", r"resourcenotfound"],
     "范围不存在 / 本期无账单",
     "订阅/计费范围不存在,或该时间段无账单数据。请核对数据源的 subscription_id / 范围配置。"),
    ("config",
     [r"invalid bigquery", r"unknown collect_mode", r"invalid.*identifier",
      r"keyerror", r"valueerror", r"not found in", r"invalid field", r"configuration",
      r"缺凭证", r"secret_data", r"blob_sas_url", r"access_token", r"缺.*配置", r"未配置"],
     "数据源配置有误 / 缺凭证配置",
     "数据源采集参数或凭证配置缺失/错误(如表名、字段、collect_mode、taiji 的 blob_sas_url)。请检查该数据源与云账号配置。"),
    ("timeout",
     [r"softtimelimitexceeded", r"timelimitexceeded", r"soft time limit",
      r"time limit exceeded"],
     "同步超时(任务运行超时被中断)",
     "该货源数据量大或云接口慢,任务超过时限被中断。建议缩小同步范围(按单月同步)或稍后重试。"),
    ("network",
     [r"timeout", r"timed out", r"connection.*reset", r"connection.*refused",
      r"getaddrinfo", r"connectionerror", r"operationalerror", r"could not connect",
      r"ssl", r"eof occurred"],
     "网络 / 临时故障",
     "网络或服务临时不可用,系统会自动重试。持续出现请检查网络与目标服务可用性。"),
]

_COMPILED = [
    (cat, [re.compile(p, re.IGNORECASE) for p in pats], title, hint)
    for cat, pats, title, hint in _RULES
]


def classify_error(error_message: str | None) -> dict:
    """把原始报错归类成 {category, title, hint}。无法识别归到 unknown。"""
    msg = (error_message or "").strip()
    if not msg:
        return {"category": "unknown", "title": "未知错误", "hint": "查看原始报错详情。"}
    for cat, pats, title, hint in _COMPILED:
        if any(p.search(msg) for p in pats):
            return {"category": cat, "title": title, "hint": hint}
    return {"category": "unknown", "title": "未知错误", "hint": "无法自动归类,请查看原始报错详情。"}


def _short(msg: str | None, n: int = 300) -> str | None:
    if not msg:
        return None
    msg = " ".join(msg.split())
    return msg if len(msg) <= n else msg[:n] + "…"


async def compute_sync_health(db: AsyncSession, visible_ds: list[int] | None) -> dict:
    """返回 {summary, sources}。visible_ds=None 表示不限(admin);[] 表示无可见范围。"""
    scope_sql = ""
    params: dict = {}
    if visible_ds is not None:
        if not visible_ds:
            return {"summary": _empty_summary(), "sources": []}
        scope_sql = "AND ds.id = ANY(:vids)"
        params["vids"] = visible_ds

    # 每个 active 货源:最新一条 sync_log + 上次成功时间 + 自上次成功以来连续失败次数
    rows = (await db.execute(text(f"""
        WITH latest AS (
            SELECT DISTINCT ON (sl.data_source_id)
                   sl.data_source_id, sl.status, sl.error_message,
                   sl.start_time, sl.end_time,
                   sl.query_start_date, sl.query_end_date, sl.records_upserted
            FROM sync_logs sl
            ORDER BY sl.data_source_id, sl.id DESC
        ),
        succ AS (
            SELECT data_source_id, MAX(end_time) AS last_success
            FROM sync_logs WHERE status='success' GROUP BY data_source_id
        ),
        fails AS (
            SELECT sl.data_source_id, COUNT(*) AS consec_fail
            FROM sync_logs sl
            LEFT JOIN succ s ON s.data_source_id = sl.data_source_id
            WHERE sl.status='failed' AND (s.last_success IS NULL OR sl.start_time > s.last_success)
            GROUP BY sl.data_source_id
        )
        SELECT ds.id, ds.name, ca.provider, ds.sync_status, ds.last_sync_at,
               l.status, l.error_message, l.start_time, l.end_time,
               l.query_start_date, l.query_end_date,
               s.last_success, COALESCE(f.consec_fail, 0) AS consec_fail
        FROM data_sources ds
        JOIN cloud_accounts ca ON ds.cloud_account_id = ca.id
        LEFT JOIN latest l ON l.data_source_id = ds.id
        LEFT JOIN succ s   ON s.data_source_id = ds.id
        LEFT JOIN fails f  ON f.data_source_id = ds.id
        WHERE ds.is_active = true {scope_sql}
        ORDER BY ds.id
    """), params)).mappings().all()

    now = dt.datetime.utcnow()  # naive UTC,与库里 utcnow() 写入的时间戳对齐
    sources = []
    summary = _empty_summary()

    for r in rows:
        status, category, reason, hint = _derive_status(r, now)
        summary["total"] += 1
        summary[status] = summary.get(status, 0) + 1
        if category:
            summary["by_category"][category] = summary["by_category"].get(category, 0) + 1

        sources.append({
            "data_source_id": r["id"],
            "name": r["name"],
            "provider": r["provider"],
            "status": status,                       # healthy/syncing/failed/stale/never
            "category": category,                   # 失败时的错误类别,否则 None
            "reason": reason,                        # 人话原因
            "hint": hint,                            # 修复建议
            "last_success_at": _iso(r["last_success"]),
            "last_attempt_at": _iso(r["start_time"]),
            "consecutive_failures": int(r["consec_fail"] or 0),
            "last_range": (
                f'{r["query_start_date"]} ~ {r["query_end_date"]}'
                if r["query_start_date"] else None
            ),
            "error_raw": _short(r["error_message"]) if status == "failed" else None,
        })

    # 有问题的排前面(failed → stale → never → syncing → healthy),同级按连续失败多的靠前
    order = {"failed": 0, "stale": 1, "never": 2, "syncing": 3, "healthy": 4}
    sources.sort(key=lambda x: (order.get(x["status"], 9), -x["consecutive_failures"]))
    summary["problem_total"] = summary["failed"] + summary["stale"]
    return {"summary": summary, "sources": sources}


def _naive(v):
    """把可能带时区的 datetime 归一成 naive UTC,便于与 utcnow() 相减。"""
    if v is not None and getattr(v, "tzinfo", None) is not None:
        return v.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return v


def _derive_status(r, now) -> tuple[str, str | None, str | None, str | None]:
    """由最新 log + 时间推导 (status, category, reason, hint)。"""
    latest_status = r["status"]
    last_success = _naive(r["last_success"])

    if latest_status is None:
        return "never", None, "从未同步", "该货源尚未产生任何同步记录。"

    if latest_status == "running":
        start = _naive(r["start_time"])
        if start and (now - start).total_seconds() > RUNNING_STALE_HOURS * 3600:
            return ("failed", "timeout", "同步疑似卡死(长时间未结束)",
                    "任务运行超时未完成,可能 worker 被回收或目标接口无响应。可重新触发同步。")
        return "syncing", None, "同步中", None

    if latest_status == "failed":
        c = classify_error(r["error_message"])
        return "failed", c["category"], c["title"], c["hint"]

    # success:看是否滞后
    if last_success and (now - last_success).total_seconds() > STALE_HOURS * 3600:
        days = int((now - last_success).total_seconds() // 86400)
        return ("stale", "stale", f"数据滞后(约 {days} 天未成功更新)",
                "最近一次成功已较久,虽无报错但可能已静默停更。建议手动同步核实。")
    return "healthy", None, None, None


def _empty_summary() -> dict:
    return {"total": 0, "healthy": 0, "syncing": 0, "failed": 0, "stale": 0,
            "never": 0, "problem_total": 0, "by_category": {}}


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else (str(v) if v else None)
