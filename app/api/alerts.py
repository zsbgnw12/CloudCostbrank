"""Alert rules & history API."""

import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func, case
from sqlalchemy.ext.asyncio import AsyncSession

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
async def list_rules(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AlertRule).order_by(AlertRule.id))
    return result.scalars().all()


@router.post("/rules/", response_model=AlertRuleRead, status_code=201)
async def create_rule(body: AlertRuleCreate, db: AsyncSession = Depends(get_db)):
    rule = AlertRule(**body.model_dump())
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.put("/rules/{rule_id}", response_model=AlertRuleRead)
async def update_rule(rule_id: int, body: AlertRuleUpdate, db: AsyncSession = Depends(get_db)):
    rule = await db.get(AlertRule, rule_id)
    if not rule:
        raise HTTPException(404, "Alert rule not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(rule, k, v)
    await db.commit()
    await db.refresh(rule)
    return rule


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(rule_id: int, db: AsyncSession = Depends(get_db)):
    rule = await db.get(AlertRule, rule_id)
    if not rule:
        raise HTTPException(404, "Alert rule not found")
    await db.delete(rule)
    await db.commit()


@router.get("/history", response_model=list[AlertHistoryRead])
async def alert_history(
    rule_id: int | None = None,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
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
):
    stmt = select(Notification).order_by(Notification.id.desc()).limit(limit)
    if unread_only:
        stmt = stmt.where(Notification.is_read.is_(False))
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/notifications/unread-count")
async def unread_count(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(func.count()).select_from(Notification).where(Notification.is_read.is_(False))
    )
    return {"count": result.scalar() or 0}


@router.post("/notifications/{notification_id}/read", status_code=204)
async def mark_read(notification_id: int, db: AsyncSession = Depends(get_db)):
    notif = await db.get(Notification, notification_id)
    if not notif:
        raise HTTPException(404, "Notification not found")
    notif.is_read = True
    await db.commit()


@router.post("/notifications/read-all", status_code=204)
async def mark_all_read(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Notification).where(Notification.is_read.is_(False))
    )
    for notif in result.scalars().all():
        notif.is_read = True
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
):
    """Return progress status for ALL active rules with a target project.
    For alerts (daily_absolute, monthly_budget, daily_increase_pct):
      progress bar fills toward threshold — triggered when actual >= threshold.
    For commitments (monthly_minimum_commitment):
      progress bar fills toward commitment — triggered (bad) when actual < threshold.
    """
    if month:
        year, mon = int(month[:4]), int(month[5:7])
    else:
        today = dt.date.today()
        year, mon = today.year, today.month

    month_start = dt.date(year, mon, 1)
    month_end = dt.date(year + 1, 1, 1) if mon == 12 else dt.date(year, mon + 1, 1)
    yesterday = dt.date.today() - dt.timedelta(days=1)

    # All active rules with a target project
    rules_result = await db.execute(
        select(AlertRule).where(
            AlertRule.is_active.is_(True),
            AlertRule.target_type == "project",
            AlertRule.target_id.isnot(None),
        )
    )
    rules = rules_result.scalars().all()
    if not rules:
        return []

    project_ids = list({r.target_id for r in rules})

    # Project info — provider 仅来自 supply_sources
    proj_result = await db.execute(
        select(Project, SupplySource)
        .join(SupplySource, Project.supply_source_id == SupplySource.id)
        .where(Project.external_project_id.in_(project_ids))
    )
    acct_map: dict[str, tuple] = {}
    for proj, ss in proj_result.all():
        acct_map[proj.external_project_id] = (proj.id, proj.name, ss.provider, proj.external_project_id)

    # Monthly cost per project
    monthly_result = await db.execute(
        select(BillingData.project_id, func.sum(BillingData.cost).label("total"))
        .join(Project, BillingData.project_id == Project.external_project_id)
        .join(SupplySource, Project.supply_source_id == SupplySource.id)
        .where(
            BillingData.provider == SupplySource.provider,
            BillingData.date >= month_start,
            BillingData.date < month_end,
            BillingData.project_id.in_(project_ids),
        )
        .group_by(BillingData.project_id)
    )
    monthly_map: dict[str, Decimal] = {r.project_id: r.total for r in monthly_result}

    daily_result = await db.execute(
        select(BillingData.project_id, func.sum(BillingData.cost).label("total"))
        .join(Project, BillingData.project_id == Project.external_project_id)
        .join(SupplySource, Project.supply_source_id == SupplySource.id)
        .where(
            BillingData.provider == SupplySource.provider,
            BillingData.date == yesterday,
            BillingData.project_id.in_(project_ids),
        )
        .group_by(BillingData.project_id)
    )
    daily_map: dict[str, Decimal] = {r.project_id: r.total for r in daily_result}

    day_before = yesterday - dt.timedelta(days=1)
    prev_daily_result = await db.execute(
        select(BillingData.project_id, func.sum(BillingData.cost).label("total"))
        .join(Project, BillingData.project_id == Project.external_project_id)
        .join(SupplySource, Project.supply_source_id == SupplySource.id)
        .where(
            BillingData.provider == SupplySource.provider,
            BillingData.date == day_before,
            BillingData.project_id.in_(project_ids),
        )
        .group_by(BillingData.project_id)
    )
    prev_daily_map: dict[str, Decimal] = {r.project_id: r.total for r in prev_daily_result}

    items: list[RuleStatus] = []
    for rule in rules:
        pid = rule.target_id
        threshold = float(rule.threshold_value)
        info = acct_map.get(pid)

        if rule.threshold_type == "daily_absolute":
            actual = float(daily_map.get(pid, Decimal("0")))
        elif rule.threshold_type == "monthly_budget" or rule.threshold_type == "monthly_minimum_commitment":
            actual = float(monthly_map.get(pid, Decimal("0")))
        elif rule.threshold_type == "daily_increase_pct":
            prev = float(prev_daily_map.get(pid, Decimal("0")))
            curr = float(daily_map.get(pid, Decimal("0")))
            actual = round(((curr - prev) / prev * 100), 2) if prev > 0 else 0
        else:
            actual = 0

        pct = round(actual / threshold * 100, 1) if threshold > 0 else 0

        if rule.threshold_type == "monthly_minimum_commitment":
            triggered = actual < threshold  # bad when UNDER
        else:
            triggered = actual >= threshold  # bad when OVER

        items.append(RuleStatus(
            rule_id=rule.id,
            rule_name=rule.name,
            threshold_type=rule.threshold_type,
            threshold_value=threshold,
            actual=round(actual, 2),
            pct=min(pct, 200),
            triggered=triggered,
            account_name=info[1] if info else pid,
            provider=info[2] if info else "unknown",
            external_project_id=pid,
        ))

    return items
