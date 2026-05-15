"""Alert rules & history API."""

import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func, case, delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.scope import (
    extract_providers_from_roles,
    has_full_access,
)
from app.models.project import Project
from app.models.supply_source import SupplySource


async def _ensure_rule_target_in_scope(
    db: AsyncSession, principal, target_type: str | None, target_id: str | None
) -> None:
    """校验告警规则的 target 必须在用户 provider 范围内。
    target_id 是单个 external_project_id 或逗号分隔列表(monthly_budget_multi)。
    """
    if has_full_access(principal):
        return
    if not target_id:
        return  # 全局规则,所有云管都可以创建
    ids = [p.strip() for p in target_id.split(",") if p.strip()]
    if not ids:
        return
    rows = (
        await db.execute(
            select(SupplySource.provider)
            .distinct()
            .join(Project, Project.supply_source_id == SupplySource.id)
            .where(Project.external_project_id.in_(ids))
        )
    ).scalars().all()
    target_providers = set(rows)
    user_providers = set(extract_providers_from_roles(principal.roles))
    out = target_providers - user_providers
    if out:
        raise HTTPException(
            403, f"Target provider(s) out of scope: {sorted(out)} (you can manage: {sorted(user_providers)})"
        )


async def _visible_rule_ids(db: AsyncSession, principal) -> list[int] | None:
    """返回该 principal 可见的 alert_rule.id 列表。None 表示全量(admin/ops)。

    可见规则:
      - 全局规则(target_id 为空):所有云管都可看
      - 单 / 多 project 规则:任一 target project 的 provider 在用户范围内 → 可看
    """
    if has_full_access(principal):
        return None
    user_providers = set(extract_providers_from_roles(principal.roles))
    if not user_providers:
        return []
    all_rules = (await db.execute(select(AlertRule))).scalars().all()
    visible: list[int] = []
    for r in all_rules:
        if not r.target_id:
            visible.append(r.id)
            continue
        ids = [p.strip() for p in r.target_id.split(",") if p.strip()]
        if not ids:
            visible.append(r.id)
            continue
        provs = (
            await db.execute(
                select(SupplySource.provider)
                .distinct()
                .join(Project, Project.supply_source_id == SupplySource.id)
                .where(Project.external_project_id.in_(ids))
            )
        ).scalars().all()
        if set(provs) & user_providers:
            visible.append(r.id)
    return visible


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
    principal: Principal = Depends(get_current_principal),
):
    stmt = select(AlertRule).order_by(AlertRule.id)
    visible = await _visible_rule_ids(db, principal)
    if visible is not None:
        if not visible:
            return []
        stmt = stmt.where(AlertRule.id.in_(visible))
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/rules/", response_model=AlertRuleRead, status_code=201)
async def create_rule(
    body: AlertRuleCreate,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_cloud_role()),
):
    # 校验 target provider 在用户范围内
    try:
        await _ensure_rule_target_in_scope(db, principal, body.target_type, body.target_id)
        rule = AlertRule(**body.model_dump())
        db.add(rule)
        await db.commit()
        await db.refresh(rule)
        return rule
    except HTTPException:
        raise
    except Exception as e:
        # 未捕获异常会让响应没 CORS header → 浏览器只看到 "Failed to fetch"。
        # 把它包装成 HTTPException(500) + log 完整 traceback，便于排查。
        import logging as _logging
        _logging.getLogger(__name__).exception(
            "create_rule failed: body=%s principal.roles=%s",
            body.model_dump(), getattr(principal, "roles", None),
        )
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        raise HTTPException(500, f"创建告警规则失败: {type(e).__name__}: {e}")


@router.put("/rules/{rule_id}", response_model=AlertRuleRead)
async def update_rule(
    rule_id: int,
    body: AlertRuleUpdate,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_cloud_role()),
):
    rule = await db.get(AlertRule, rule_id)
    if not rule:
        raise HTTPException(404, "Alert rule not found")
    # 防越权:先校验现有 rule 的 target 在用户范围(防修改别人范围的 rule)
    await _ensure_rule_target_in_scope(db, principal, rule.target_type, rule.target_id)
    # 如果改了 target,新 target 也要在用户范围
    changes = body.model_dump(exclude_unset=True)
    new_target = changes.get("target_id", rule.target_id)
    new_type = changes.get("target_type", rule.target_type)
    if "target_id" in changes or "target_type" in changes:
        await _ensure_rule_target_in_scope(db, principal, new_type, new_target)
    for k, v in changes.items():
        setattr(rule, k, v)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(
    rule_id: int,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_cloud_role()),
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
    # 防越权:cloud_<provider> 用户只能删自己范围内的 rule
    await _ensure_rule_target_in_scope(db, principal, rule.target_type, rule.target_id)

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
    principal: Principal = Depends(get_current_principal),
):
    stmt = select(AlertHistory).order_by(AlertHistory.id.desc()).limit(limit)
    if rule_id:
        stmt = stmt.where(AlertHistory.rule_id == rule_id)
    # 数据范围过滤:cloud_<provider> 只看自己 visible rule 的 history
    visible_rule_ids = await _visible_rule_ids(db, principal)
    if visible_rule_ids is not None:
        if not visible_rule_ids:
            return []
        stmt = stmt.where(AlertHistory.rule_id.in_(visible_rule_ids))
    result = await db.execute(stmt)
    return result.scalars().all()


# ─── Notifications ─────────────────────────────────────────────

def _scope_notifications_stmt(stmt, visible_rule_ids: list[int] | None):
    """把通知 SELECT 限制在用户可见 rule 内。
    visible_rule_ids=None  → 全访问(admin/ops),不加过滤
    visible_rule_ids=[]    → 用户没任何可见 rule,直接 false
    其它情况 → 通知必须挂在可见 rule 的 history 上(orphan 通知 alert_history_id=NULL
              当作系统通知,所有人都能看)
    """
    if visible_rule_ids is None:
        return stmt
    if not visible_rule_ids:
        return stmt.where(Notification.id == -1)  # 永假
    sub = select(AlertHistory.id).where(AlertHistory.rule_id.in_(visible_rule_ids))
    return stmt.where(
        (Notification.alert_history_id.is_(None)) |
        (Notification.alert_history_id.in_(sub))
    )


async def _ensure_notification_visible(
    db: AsyncSession, principal: Principal, notif: Notification
) -> None:
    """单条通知的越权校验。alert_history_id=NULL 视为系统通知,放行;
    否则关联到 rule,要求 rule 在用户可见范围内。"""
    visible = await _visible_rule_ids(db, principal)
    if visible is None:
        return
    if notif.alert_history_id is None:
        return
    hist = await db.get(AlertHistory, notif.alert_history_id)
    if hist is None or hist.rule_id not in (visible or []):
        raise HTTPException(403, "Forbidden")


@router.get("/notifications", response_model=list[NotificationRead])
async def list_notifications(
    unread_only: bool = False,
    limit: int = Query(30, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    stmt = select(Notification).order_by(Notification.id.desc()).limit(limit)
    if unread_only:
        stmt = stmt.where(Notification.is_read.is_(False))
    visible = await _visible_rule_ids(db, principal)
    stmt = _scope_notifications_stmt(stmt, visible)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/notifications/unread-count")
async def unread_count(
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    stmt = select(func.count()).select_from(Notification).where(Notification.is_read.is_(False))
    visible = await _visible_rule_ids(db, principal)
    stmt = _scope_notifications_stmt(stmt, visible)
    result = await db.execute(stmt)
    return {"count": result.scalar() or 0}


@router.post("/notifications/{notification_id}/read", status_code=204)
async def mark_read(
    notification_id: int,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    notif = await db.get(Notification, notification_id)
    if not notif:
        raise HTTPException(404, "Notification not found")
    await _ensure_notification_visible(db, principal, notif)
    notif.is_read = True
    await db.commit()


@router.post("/notifications/read-all", status_code=204)
async def mark_all_read(
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    stmt = select(Notification).where(Notification.is_read.is_(False))
    visible = await _visible_rule_ids(db, principal)
    stmt = _scope_notifications_stmt(stmt, visible)
    result = await db.execute(stmt)
    for notif in result.scalars().all():
        notif.is_read = True
    await db.commit()


@router.delete("/notifications/{notification_id}", status_code=204)
async def delete_notification(
    notification_id: int,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    """删除单条通知。"""
    notif = await db.get(Notification, notification_id)
    if not notif:
        raise HTTPException(404, "Notification not found")
    await _ensure_notification_visible(db, principal, notif)
    await db.delete(notif)
    await db.commit()


@router.delete("/notifications", status_code=204)
async def delete_all_notifications(
    only_read: bool = Query(False, description="若为 true 只删已读;false 删全部"),
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    """批量清空通知。only_read=true 只删已读;否则全删。"""
    stmt = select(Notification)
    if only_read:
        stmt = stmt.where(Notification.is_read.is_(True))
    visible = await _visible_rule_ids(db, principal)
    stmt = _scope_notifications_stmt(stmt, visible)
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
    principal: Principal = Depends(get_current_principal),
):
    """Return progress status for ALL active rules.

    支持 6 种 threshold_type:
      - daily_absolute / monthly_budget / daily_increase_pct → 单 project,触发 = 超阈值
      - monthly_minimum_commitment   → 单 project,触发(坏)= 低于承诺
      - account_lifetime_quota       → 单 project,actual = 全期累计
      - monthly_budget_multi         → 多 project(target_id 逗号分隔),actual = 该组本月合计
      - yearly_budget_multi          → 多 project(target_id 逗号分隔),actual = 该组本年合计
    """
    if month:
        year, mon = int(month[:4]), int(month[5:7])
    else:
        today = dt.date.today()
        year, mon = today.year, today.month

    month_start = dt.date(year, mon, 1)
    month_end = dt.date(year + 1, 1, 1) if mon == 12 else dt.date(year, mon + 1, 1)
    year_start = dt.date(year, 1, 1)
    year_end = dt.date(year + 1, 1, 1)
    yesterday = dt.date.today() - dt.timedelta(days=1)

    # 拉所有 active 规则(不再限 target_type='project',否则丢 multi 等新类型)
    rules_stmt = select(AlertRule).where(
        AlertRule.is_active.is_(True),
        AlertRule.target_id.isnot(None),
    )
    # 数据范围:cloud_<provider> 用户只看自己 visible rule
    visible_rule_ids = await _visible_rule_ids(db, principal)
    if visible_rule_ids is not None:
        if not visible_rule_ids:
            return []
        rules_stmt = rules_stmt.where(AlertRule.id.in_(visible_rule_ids))
    rules = (await db.execute(rules_stmt)).scalars().all()
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

    # Per-project yearly cost (当年累计 — 给 yearly_budget_multi 用)
    yearly_result = await db.execute(
        select(BillingData.project_id, func.sum(BillingData.cost).label("total"))
        .where(
            BillingData.date >= year_start,
            BillingData.date < year_end,
            BillingData.project_id.in_(project_ids),
        )
        .group_by(BillingData.project_id)
    )
    yearly_map: dict[str, Decimal] = {r.project_id: r.total for r in yearly_result}

    # 自定义时间段费用映射（为每个规则单独查询）
    custom_period_map: dict[int, dict[str, Decimal]] = {}
    for rule in rules:
        if rule.threshold_type == "custom_period_budget_multi" and rule.start_date and rule.end_date:
            period_end = rule.end_date + dt.timedelta(days=1)
            custom_result = await db.execute(
                select(BillingData.project_id, func.sum(BillingData.cost).label("total"))
                .where(
                    BillingData.date >= rule.start_date,
                    BillingData.date < period_end,
                    BillingData.project_id.in_(project_ids),
                )
                .group_by(BillingData.project_id)
            )
            custom_period_map[rule.id] = {r.project_id: r.total for r in custom_result}

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
        elif rule.threshold_type == "yearly_budget_multi":
            # 多项目本年合计
            actual = float(sum(yearly_map.get(p, Decimal("0")) for p in pids))
            triggered = actual >= threshold
            display_name = f"{len(pids)} 个项目"
            display_provider = "multi"
            display_pid = rule.target_id or ""
        elif rule.threshold_type == "custom_period_budget_multi":
            # 自定义时间段多项目合计
            period_map = custom_period_map.get(rule.id, {})
            actual = float(sum(period_map.get(p, Decimal("0")) for p in pids))
            triggered = actual >= threshold
            period_desc = ""
            if rule.start_date and rule.end_date:
                period_desc = f" ({rule.start_date.strftime('%Y-%m-%d')} ~ {rule.end_date.strftime('%Y-%m-%d')})"
            display_name = f"{len(pids)} 个项目{period_desc}"
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
