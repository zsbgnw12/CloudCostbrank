"""Alert checking service."""

import datetime as dt
import logging
import smtplib
from decimal import Decimal
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx
from sqlalchemy import func, create_engine
from sqlalchemy.orm import Session

from app.config import settings
from app.models.alert import AlertRule, AlertHistory, Notification
from app.models.billing import BillingData
from app.models.project import Project
from app.models.supply_source import SupplySource

# 服务账号配额告警的固定触发百分比(产品规则:达到配额的 90% 即告警)。
# 想做成可配置的话,把它挪到 alert_rules 加列存。
ACCOUNT_QUOTA_TRIGGER_PCT = 0.9

logger = logging.getLogger(__name__)

_sync_engine = None


def _get_sync_engine():
    global _sync_engine
    if _sync_engine is None:
        _sync_engine = create_engine(settings.SYNC_DATABASE_URL, pool_size=3)
    return _sync_engine


def check_all_alerts():
    """Check all active alert rules and trigger if thresholds are exceeded."""
    engine = _get_sync_engine()
    today = dt.date.today()
    yesterday = today - dt.timedelta(days=1)

    with Session(engine) as session:
        rules = session.query(AlertRule).filter(AlertRule.is_active.is_(True)).all()

        for rule in rules:
            try:
                if rule.threshold_type == "monthly_minimum_commitment":
                    _check_monthly_commitment(session, rule, today)
                elif rule.threshold_type == "monthly_budget":
                    _check_monthly_budget(session, rule, today)
                elif rule.threshold_type == "daily_increase_pct":
                    _check_daily_increase_pct(session, rule, yesterday)
                elif rule.threshold_type == "account_lifetime_quota":
                    _check_account_lifetime_quota(session, rule)
                elif rule.threshold_type == "monthly_budget_multi":
                    _check_monthly_budget_multi(session, rule, today)
                else:
                    actual_value = _evaluate_rule(session, rule, yesterday, today)
                    if actual_value is not None and actual_value >= rule.threshold_value:
                        _trigger_alert(session, rule, actual_value)
            except Exception as e:
                logger.error(f"Error checking alert rule {rule.id}: {e}")


def _check_monthly_budget(session: Session, rule: AlertRule, today: dt.date):
    """Check if monthly accumulated cost exceeds the budget threshold."""
    month_start = today.replace(day=1)
    month_end = today + dt.timedelta(days=1)

    actual_value = _get_monthly_cost(session, rule, month_start, month_end)
    if actual_value is not None and actual_value >= rule.threshold_value:
        message = (
            f"告警 [{rule.name}]: 月度预算超标！"
            f"预算: ${rule.threshold_value}, 实际: ${actual_value}"
        )
        _trigger_alert(session, rule, actual_value, custom_message=message)


def _check_daily_increase_pct(session: Session, rule: AlertRule, yesterday: dt.date):
    """Check if yesterday's cost increased by more than threshold % vs day before."""
    day_before = yesterday - dt.timedelta(days=1)

    prev_cost = _get_daily_cost(session, rule, day_before)
    curr_cost = _get_daily_cost(session, rule, yesterday)

    if prev_cost is None or curr_cost is None or prev_cost <= 0:
        return

    increase_pct = float((curr_cost - prev_cost) / prev_cost * 100)
    if increase_pct >= float(rule.threshold_value):
        message = (
            f"告警 [{rule.name}]: 日费用环比增长 {increase_pct:.1f}%，"
            f"超过阈值 {rule.threshold_value}%！"
            f"昨日: ${curr_cost}, 前日: ${prev_cost}"
        )
        _trigger_alert(session, rule, Decimal(str(round(increase_pct, 2))), custom_message=message)


def _check_monthly_budget_multi(session: Session, rule: AlertRule, today: dt.date):
    """多 project 月费用合计配额告警:
    一组 project 的本月费用合计 ≥ 阈值 时触发。

    rule 字段含义约定(避免改 schema):
      - target_type     : "project_group"
      - target_id       : 多个 project.external_project_id 用逗号分隔,如 "p1,p2,p3,p4"
      - threshold_value : 该组 project 本月预算合计 USD

    使用场景:客户买了 4 个 project 各 10k 预算,实际只用 2 个,
    用单 project 月预算 monthly_budget 看不全;用此规则看 4 个合计是否超 40k。
    """
    quota = float(rule.threshold_value or 0)
    if quota <= 0:
        return
    raw_ids = (rule.target_id or "").strip()
    if not raw_ids:
        return
    project_ids = [p.strip() for p in raw_ids.split(",") if p.strip()]
    if not project_ids:
        return

    month_start = today.replace(day=1)
    month_end = today + dt.timedelta(days=1)

    used = session.query(func.coalesce(func.sum(BillingData.cost), 0)).filter(
        BillingData.project_id.in_(project_ids),
        BillingData.date >= month_start,
        BillingData.date < month_end,
    ).scalar() or Decimal("0")
    used_f = float(used)

    if used_f < quota:
        return

    pct = (used_f / quota * 100) if quota else 0
    proj_desc = f"{len(project_ids)} 个项目({', '.join(project_ids[:3])}{'...' if len(project_ids) > 3 else ''})"
    message = (
        f"告警 [{rule.name}]: 多项目月费用合计超预算!"
        f" {proj_desc} 本月累计 ${used_f:.2f},预算 ${quota:.2f} (使用率 {pct:.1f}%)"
    )
    _trigger_alert(session, rule, Decimal(str(round(used_f, 2))), custom_message=message)


def _check_account_lifetime_quota(session: Session, rule: AlertRule):
    """单服务账号生命周期总费用配额告警。
    累计费用(从 billing_summary 全部历史 SUM) ≥ 配额 × ACCOUNT_QUOTA_TRIGGER_PCT(默认 90%) 时告警。

    rule 字段含义约定(避免改 schema):
      - target_type     : "project"(沿用现有,匹配 project.external_project_id)
      - target_id       : project.external_project_id (如 "chuer-2026021801")
      - threshold_value : 总配额上限 USD (如 1000)
    """
    quota = float(rule.threshold_value or 0)
    if quota <= 0:
        return
    target_id = (rule.target_id or "").strip()
    if not target_id:
        return

    used = session.query(func.coalesce(func.sum(BillingData.cost), 0)).filter(
        BillingData.project_id == target_id
    ).scalar() or Decimal("0")
    used_f = float(used)
    threshold_amount = quota * ACCOUNT_QUOTA_TRIGGER_PCT

    if used_f < threshold_amount:
        return

    pct = (used_f / quota * 100) if quota else 0
    message = (
        f"告警 [{rule.name}]: 服务账号 {target_id} 总配额预警!"
        f" 累计已用 ${used_f:.2f},配额 ${quota:.2f} (使用率 {pct:.1f}%,"
        f"触发阈值 {int(ACCOUNT_QUOTA_TRIGGER_PCT * 100)}%)"
    )
    _trigger_alert(session, rule, Decimal(str(round(used_f, 2))), custom_message=message)


def _get_daily_cost(session: Session, rule: AlertRule, day: dt.date) -> Decimal | None:
    """Get total cost for a single day for the rule's target."""
    next_day = day + dt.timedelta(days=1)
    query = session.query(func.sum(BillingData.cost)).filter(
        BillingData.date >= day,
        BillingData.date < next_day,
    )
    if rule.target_type == "project" and rule.target_id:
        query = query.filter(BillingData.project_id == rule.target_id)
    elif rule.target_type == "provider" and rule.target_id:
        query = query.filter(BillingData.provider == rule.target_id)
    return query.scalar()


def _check_monthly_commitment(session: Session, rule: AlertRule, today: dt.date):
    """Check monthly minimum commitment at end of month or on the 1st for previous month."""
    # Run on the 1st of each month to check previous month,
    # or on any day to check if current month is on track
    if today.day == 1:
        # Check previous month
        last_day_prev = today - dt.timedelta(days=1)
        month_start = last_day_prev.replace(day=1)
        month_end = today
    else:
        # Mid-month: skip (only alert at month end)
        # But also check on last day of month
        import calendar
        _, last_day = calendar.monthrange(today.year, today.month)
        if today.day != last_day:
            return
        month_start = today.replace(day=1)
        month_end = today + dt.timedelta(days=1)

    actual_value = _get_monthly_cost(session, rule, month_start, month_end)
    if actual_value is not None and actual_value < rule.threshold_value:
        gap = rule.threshold_value - actual_value
        message = (
            f"告警 [{rule.name}]: 月最低承诺用量未达标！"
            f"承诺: ${rule.threshold_value}, 实际: ${actual_value}, 差额: ${gap}"
        )
        _trigger_alert(session, rule, actual_value, custom_message=message)


def _get_monthly_cost(session: Session, rule: AlertRule, start: dt.date, end: dt.date) -> Decimal | None:
    """Get total cost for a month for the rule's target."""
    query = session.query(func.sum(BillingData.cost)).filter(
        BillingData.date >= start,
        BillingData.date < end,
    )
    if rule.target_type == "project" and rule.target_id:
        query = query.filter(BillingData.project_id == rule.target_id)
    elif rule.target_type == "provider" and rule.target_id:
        query = query.filter(BillingData.provider == rule.target_id)
    return query.scalar()


def _evaluate_rule(session: Session, rule: AlertRule, start: dt.date, end: dt.date) -> Decimal | None:
    """Evaluate a single rule and return the actual value."""
    query = session.query(func.sum(BillingData.cost)).filter(
        BillingData.date >= start,
        BillingData.date < end,
    )

    if rule.target_type == "project" and rule.target_id:
        query = query.filter(BillingData.project_id == rule.target_id)
    elif rule.target_type == "provider" and rule.target_id:
        query = query.filter(BillingData.provider == rule.target_id)

    return query.scalar()


def _trigger_alert(session: Session, rule: AlertRule, actual_value: Decimal, *, custom_message: str | None = None):
    """Create alert history record, in-app notification, and send email/webhook."""
    message = custom_message or f"Alert [{rule.name}]: actual={actual_value}, threshold={rule.threshold_value}"

    history = AlertHistory(
        rule_id=rule.id,
        triggered_at=dt.datetime.utcnow(),
        actual_value=actual_value,
        threshold_value=rule.threshold_value,
        message=message,
        notified=False,
    )
    session.add(history)
    session.flush()

    # Create in-app notification
    notif = Notification(
        title="费用告警" if rule.threshold_type != "monthly_minimum_commitment" else "承诺用量未达标",
        message=message,
        type="warning",
        alert_history_id=history.id,
    )
    session.add(notif)
    session.commit()

    # Send email notification
    if rule.notify_email:
        try:
            _send_email(rule.notify_email, f"CloudCost 告警: {rule.name}", message)
            history.notified = True
            session.commit()
        except Exception as e:
            logger.error(f"Failed to send email for alert rule {rule.id}: {e}")

    # Send webhook notification if configured
    if rule.notify_webhook:
        try:
            with httpx.Client(timeout=10) as client:
                client.post(rule.notify_webhook, json={"msg_type": "text", "content": {"text": message}})
            history.notified = True
            session.commit()
        except Exception as e:
            logger.error(f"Failed to send webhook for alert rule {rule.id}: {e}")


def _send_email(to_addrs: str, subject: str, body: str):
    """Send alert email via SMTP."""
    if not settings.SMTP_HOST:
        logger.warning("SMTP not configured, skipping email")
        return

    recipients = [addr.strip() for addr in to_addrs.split(",") if addr.strip()]
    if not recipients:
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_FROM
    msg["To"] = ", ".join(recipients)

    html = f"""\
<html><body style="font-family: sans-serif; background: #1a1a2e; color: #e5e5e5; padding: 20px;">
<div style="max-width: 600px; margin: auto; background: #16213e; border-radius: 8px; padding: 24px;">
<h2 style="color: #e94560; margin-top: 0;">⚠️ CloudCost 告警</h2>
<p style="font-size: 16px; line-height: 1.6;">{body}</p>
<hr style="border-color: #3a3a5c;">
<p style="font-size: 12px; color: #a1a1aa;">此邮件由 CloudCost 系统自动发送</p>
</div></body></html>"""

    msg.attach(MIMEText(body, "plain"))
    msg.attach(MIMEText(html, "html"))

    # 三种连接方式，按优先级：
    #   1. SMTP_USE_SSL=True → 隐式 SSL（常用端口 465，国内 189/QQ/网易）
    #   2. SMTP_USE_TLS=True → STARTTLS（常用端口 587，Gmail/Outlook）
    #   3. 都关 → 明文（仅限内网调试）
    if settings.SMTP_USE_SSL:
        with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            if settings.SMTP_USER:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(settings.SMTP_FROM, recipients, msg.as_string())
    elif settings.SMTP_USE_TLS:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            server.starttls()
            if settings.SMTP_USER:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(settings.SMTP_FROM, recipients, msg.as_string())
    else:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
            if settings.SMTP_USER:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(settings.SMTP_FROM, recipients, msg.as_string())
