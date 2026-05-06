"""Alert rules & history API."""

import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func, case, delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_principal, require_cloud_role, require_roles
from app.auth.principal import Principal
from app.database import get_db
from app.models.alert import AlertRule, AlertHistory, Notification
from app.models.billing import BillingData
from app.models.cloud_account import CloudAccount
from app.models.data_source import DataSource
from app.models.project import Project
from app.models.supply_source import SupplySource
from app.schemas.billing import (
    AlertRuleCreate, AlertRuleUpdate, AlertRuleRead,
    AlertHistoryRead, NotificationRead,
)

router = APIRouter()


# ─── Rules CRUD ────────────────────────────────────────────────

@router.get("/rules/", response_model=list[AlertRuleRead])
async def list_rules(
    db: AsyncSession = Depends(get_db),
    _: Principal = Depends(get_current_principal),
):
    result = await db.execute(select(AlertRule).order_by(AlertRule.id))
    return result.scalars().all()


@router.post("/rules/", response_model=AlertRuleRead, status_code=201)
async def create_rule(
    body: AlertRuleCreate,
    db: AsyncSession = Depends(get_db),
    _: Principal = Depends(require_cloud_role()),
):
    rule = AlertRule(**body.model_dump())
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.put("/rules/{rule_id}", response_model=AlertRuleRead)
async def update_rule(
    rule_id: int,
    body: AlertRuleUpdate,
    db: AsyncSession = Depends(get_db),
    _: Principal = Depends(require_cloud_role()),
):
    rule = await db.get(AlertRule, rule_id)
    if not rule:
        raise HTTPException(404, "Alert rule not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(rule, k, v)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(
    rule_id: int,
    db: AsyncSession = Depends(get_db),
    _: Principal = Depends(require_cloud_role()),
):
    """删除告警规则。FK 依赖链:Notification → AlertHistory → AlertRule。
    schema 没设 ondelete CASCADE,所以接口里手工按链清理:
      1. 把所有引用该 rule 历史的 notifications 的 alert_history_id 置 NULL
         (Notification.alert_history_id 是 nullable,所以可解关联但保留通知正文)
      2. 删除该 rule 的所有 history
      3. 删 rule 本身
    """
    rule = await db.get(AlertRule, rule_id)
    if not rule:
        raise HTTPException(404, "Alert rule not found")

    # 先收集该 rule 关联的所有 history id
    hist_ids = list(
        (await db.execute(
            select(AlertHistory.id).where(AlertHistory.rule_id == rule_id)
        )).scalars().all()
    )
    if hist_ids:
        # notifications.alert_history_id → NULL(保留通知,只解 FK 关联)
        await db.execute(
            update(Notification)
            .where(Notification.alert_history_id.in_(hist_ids))
            .values(alert_history_id=None)
        )
        # 删 history
        await db.execute(
            delete(AlertHistory).where(AlertHistory.rule_id == rule_id)
        )

    await db.delete(rule)
    await db.commit()


@router.get("/history", response_model=list[AlertHistoryRead])
async def alert_history(
    rule_id: int | None = None,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: Principal = Depends(get_current_principal),
):
    stmt = select(AlertHistory).order_by(AlertHistory.id.desc()).limit(limit)
    if rule_id:
        stmt = stmt.where(AlertHistory.rule_id == rule_id)
    result = await db.execute(stmt)
    return result.scalars().all()


# ─── Notifications ─────────────────────────────────────────────

@router.get("/notifications", response_model=list[NotificationRead])
async def list_notifications(
    unread_only: bool = False,
    limit: int = Query(30, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: Principal = Depends(get_current_principal),
):
    stmt = select(Notification).order_by(Notification.id.desc()).limit(limit)
    if unread_only:
        stmt = stmt.where(Notification.is_read.is_(False))
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/notifications/unread-count")
async def unread_count(
    db: AsyncSession = Depends(get_db),
    _: Principal = Depends(get_current_principal),
):
    result = await db.execute(
        select(func.count()).select_from(Notification).where(Notification.is_read.is_(False))
    )
    return {"count": result.scalar() or 0}


@router.post("/notifications/{notification_id}/read", status_code=204)
async def mark_read(
    notification_id: int,
    db: AsyncSession = Depends(get_db),
    _: Principal = Depends(get_current_principal),
):
    notif = await db.get(Notification, notification_id)
    if not notif:
        raise HTTPException(404, "Notification not found")
    notif.is_read = True
    await db.commit()


@router.post("/notifications/read-all", status_code=204)
async def mark_all_read(
    db: AsyncSession = Depends(get_db),
    _: Principal = Depends(get_current_principal),
):
    result = await db.execute(
        select(Notification).where(Notification.is_read.is_(False))
    )
    for notif in result.scalars().all():
        notif.is_read = True
    await db.commit()


@router.delete("/notifications/{notification_id}", status_code=204)
async def delete_notification(
    notification_id: int,
    db: AsyncSession = Depends(get_db),
    _: Principal = Depends(get_current_principal),
):
    """删除单条通知。"""
    notif = await db.get(Notification, notification_id)
    if not notif:
        raise HTTPException(404, "Notification not found")
    await db.delete(notif)
    await db.commit()


@router.delete("/notifications", status_code=204)
async def delete_all_notifications(
    only_read: bool = Query(False, description="若为 true 只删已读;false 删全部"),
    db: AsyncSession = Depends(get_db),
    _: Principal = Depends(get_current_principal),
):
    """批量清空通知。only_read=true 只删已读;否则全删。"""
    stmt = select(Notification)
    if only_read:
        stmt = stmt.where(Notification.is_read.is_(True))
    result = await db.execute(stmt)
    rows = result.scalars().all()
    for n in rows:
        await db.delete(n)
    await db.commit()


# ─── Commitment Status (for charts) ───────────────────────────

class CommitmentStatus(BaseModel):
    account_id: int
    account_name: str
    provider: str
    external_project_id: str
    commitment: float
    actual: float
    gap: float
    met: bool


class RuleStatus(BaseModel):
    rule_id: int
    rule_name: str
    threshold_type: str
    threshold_value: float
    actual: float
    pct: float  # actual / threshold * 100
    triggered: bool  # whether the alert condition is met
    account_name: str
    provider: str
    external_project_id: str


@router.get("/rule-status", response_model=list[RuleStatus])
async def rule_status(
    month: str = Query(None, description="YYYY-MM, defaults to current month"),
    db: AsyncSession = Depends(get_db),
    _: Principal = Depends(get_current_principal),
):
    """Return progress status for ALL active rules.

    支持 5 种 threshold_type:
      - daily_absolute / monthly_budget / daily_increase_pct → 单 project,触发 = 超阈值
      - monthly_minimum_commitment   → 单 project,触发(坏)= 低于承诺
      - account_lifetime_quota       → 单 project,actual = 全期累计
      - monthly_budget_multi         → 多 project(target_id 逗号分隔),actual = 该组本月合计
    """
    if month:
        year, mon = int(month[:4]), int(month[5:7])
    else:
        today = dt.date.today()
        year, mon = today.year, today.month

    month_start = dt.date(year, mon, 1)
    month_end = dt.date(year + 1, 1, 1) if mon == 12 else dt.date(year, mon + 1, 1)
    yesterday = dt.date.today() - dt.timedelta(days=1)

    # 拉所有 active 规则(不再限 target_type='project',否则丢 multi 等新类型)
    rules_result = await db.execute(
        select(AlertRule).where(
            AlertRule.is_active.is_(True),
            AlertRule.target_id.isnot(None),
        )
    )
    rules = rules_result.scalars().all()
    if not rules:
        return []

    # 收集所有 target_id 涉及的 project external_id(单 + 多 group 拆开)
    all_project_ids: set[str] = set()
    rule_pids: dict[int, list[str]] = {}  # rule.id → 该规则关联的 project_ids
    for r in rules:
        if r.target_id and "," in r.target_id:
            ids = [p.strip() for p in r.target_id.split(",") if p.strip()]
        else:
            ids = [r.target_id] if r.target_id else []
        rule_pids[r.id] = ids
        all_project_ids.update(ids)

    if not all_project_ids:
        return []

    project_ids = list(all_project_ids)

    # Project info(provider 仅来自 supply_sources)
    proj_result = await db.execute(
        select(Project, SupplySource)
        .join(SupplySource, Project.supply_source_id == SupplySource.id)
        .where(Project.external_project_id.in_(project_ids))
    )
    acct_map: dict[str, tuple] = {}
    for proj, ss in proj_result.all():
        acct_map[proj.external_project_id] = (proj.id, proj.name, ss.provider, proj.external_project_id)

    # Per-project monthly cost(本月)
    monthly_result = await db.execute(
        select(BillingData.project_id, func.sum(BillingData.cost).label("total"))
        .where(
            BillingData.date >= month_start,
            BillingData.date < month_end,
            BillingData.project_id.in_(project_ids),
        )
        .group_by(BillingData.project_id)
    )
    monthly_map: dict[str, Decimal] = {r.project_id: r.total for r in monthly_result}

    # Per-project lifetime cost(全期累计 — 给 account_lifetime_quota 用)
    lifetime_result = await db.execute(
        select(BillingData.project_id, func.sum(BillingData.cost).label("total"))
        .where(BillingData.project_id.in_(project_ids))
        .group_by(BillingData.project_id)
    )
    lifetime_map: dict[str, Decimal] = {r.project_id: r.total for r in lifetime_result}

    daily_result = await db.execute(
        select(BillingData.project_id, func.sum(BillingData.cost).label("total"))
        .where(
            BillingData.date == yesterday,
            BillingData.project_id.in_(project_ids),
        )
        .group_by(BillingData.project_id)
    )
    daily_map: dict[str, Decimal] = {r.project_id: r.total for r in daily_result}

    day_before = yesterday - dt.timedelta(days=1)
    prev_daily_result = await db.execute(
        select(BillingData.project_id, func.sum(BillingData.cost).label("total"))
        .where(
            BillingData.date == day_before,
            BillingData.project_id.in_(project_ids),
        )
        .group_by(BillingData.project_id)
    )
    prev_daily_map: dict[str, Decimal] = {r.project_id: r.total for r in prev_daily_result}

    items: list[RuleStatus] = []
    for rule in rules:
        threshold = float(rule.threshold_value)
        pids = rule_pids.get(rule.id, [])

        if rule.threshold_type == "monthly_budget_multi":
            # 多项目本月合计
            actual = float(sum(monthly_map.get(p, Decimal("0")) for p in pids))
            triggered = actual >= threshold
            display_name = f"{len(pids)} 个项目"
            display_provider = "multi"
            display_pid = rule.target_id or ""
        elif rule.threshold_type == "account_lifetime_quota":
            pid = pids[0] if pids else ""
            info = acct_map.get(pid)
            actual = float(lifetime_map.get(pid, Decimal("0")))
            # 90% 即触发
            triggered = actual >= threshold * 0.9
            display_name = info[1] if info else pid
            display_provider = info[2] if info else "unknown"
            display_pid = pid
        else:
            # 单 project 类型
            pid = pids[0] if pids else ""
            info = acct_map.get(pid)
            display_name = info[1] if info else pid
            display_provider = info[2] if info else "unknown"
            display_pid = pid

            if rule.threshold_type == "daily_absolute":
                actual = float(daily_map.get(pid, Decimal("0")))
                triggered = actual >= threshold
            elif rule.threshold_type == "monthly_budget":
                actual = float(monthly_map.get(pid, Decimal("0")))
                triggered = actual >= threshold
            elif rule.threshold_type == "monthly_minimum_commitment":
                actual = float(monthly_map.get(pid, Decimal("0")))
                triggered = actual < threshold  # 承诺 bad when UNDER
            elif rule.threshold_type == "daily_increase_pct":
                prev = float(prev_daily_map.get(pid, Decimal("0")))
                curr = float(daily_map.get(pid, Decimal("0")))
                actual = round(((curr - prev) / prev * 100), 2) if prev > 0 else 0
                triggered = actual >= threshold
            else:
                actual = 0
                triggered = False

        pct = round(actual / threshold * 100, 1) if threshold > 0 else 0

        items.append(RuleStatus(
            rule_id=rule.id,
            rule_name=rule.name,
            threshold_type=rule.threshold_type,
            threshold_value=threshold,
            actual=round(actual, 2),
            pct=min(pct, 200),
            triggered=triggered,
            account_name=display_name,
            provider=display_provider,
            external_project_id=display_pid,
        ))

    return items
