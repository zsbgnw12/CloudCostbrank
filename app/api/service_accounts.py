"""Service Accounts API — unified view over CloudAccount + DataSource + Project.

云厂商(provider)仅来自 supply_sources；供应商名称仅来自 suppliers。projects 不重复存 provider/group_label。
"""

import datetime as dt
import io
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.billing import BillingData
from app.models.cloud_account import CloudAccount
from app.models.data_source import DataSource
from app.models.project import Project
from app.models.project_assignment_log import ProjectAssignmentLog
from app.models.supplier import Supplier
from app.models.supply_source import SupplySource
from app.services.crypto_service import encrypt_dict, decrypt_to_dict
from app.services.default_supply_sources import ensure_other_gcp_supply_source_id

router = APIRouter()


def _data_source_config_for_create(provider: str, external_project_id: str) -> dict:
    """DataSource.config for collectors. Azure needs subscription_id (same as Project.external_project_id)."""
    base: dict = {"auto_created": True}
    if provider == "azure":
        base["subscription_id"] = external_project_id.strip()
        base["collect_mode"] = "subscription"
        base["cost_metric"] = "ActualCost"
    return base


async def _cloud_provider(db: AsyncSession, project: Project) -> str:
    ss = await db.get(SupplySource, project.supply_source_id)
    if not ss:
        raise HTTPException(500, "Project 缺少有效货源")
    return ss.provider


# ─── Schemas ───────────────────────────────────────────────────

class ServiceAccountCreate(BaseModel):
    supply_source_id: int
    name: str
    external_project_id: str
    secret_data: dict[str, Any] = {}
    notes: str | None = None
    order_method: str | None = None

    @field_validator("name", "external_project_id", mode="before")
    @classmethod
    def strip_whitespace(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip()
        return v

    @field_validator("order_method", mode="before")
    @classmethod
    def strip_order_method(cls, v: object) -> object:
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            return s if s else None
        return v


class ServiceAccountUpdate(BaseModel):
    name: str | None = None
    supply_source_id: int | None = None
    external_project_id: str | None = None
    secret_data: dict[str, Any] | None = None
    notes: str | None = None
    order_method: str | None = None

    @field_validator("name", "external_project_id", mode="before")
    @classmethod
    def strip_whitespace(cls, v: object) -> object:
        if isinstance(v, str):
            return v.strip()
        return v

    @field_validator("order_method", mode="before")
    @classmethod
    def strip_order_method(cls, v: object) -> object:
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            return s if s else None
        return v


class ServiceAccountListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    supply_source_id: int
    supplier_name: str
    provider: str  # 来自 supply_sources，非 projects 列
    external_project_id: str
    status: str
    order_method: str | None = None
    created_at: dt.datetime


class HistoryItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    action: str
    from_status: str | None
    to_status: str | None
    operator: str | None
    notes: str | None
    created_at: dt.datetime


class ServiceAccountDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    supply_source_id: int
    supplier_id: int
    supplier_name: str
    provider: str
    external_project_id: str
    status: str
    notes: str | None
    order_method: str | None = None
    secret_fields: list[str]
    created_at: dt.datetime
    history: list[HistoryItem]


class CostByService(BaseModel):
    service: str
    cost: float
    usage_quantity: float
    usage_unit: str | None


class DailyCost(BaseModel):
    date: str
    cost: float
    usage_quantity: float


class DailyServiceCost(BaseModel):
    date: str
    service: str
    cost: float
    usage_quantity: float
    usage_unit: str | None


class CostSummary(BaseModel):
    total_cost: float
    total_usage: float
    services: list[CostByService]
    daily: list[DailyCost]
    daily_by_service: list[DailyServiceCost]


# ─── Helpers ───────────────────────────────────────────────────

def _log(db, project, action: str, from_status: str, to_status: str, notes: str | None = None):
    db.add(ProjectAssignmentLog(
        project_id=project.id, action=action,
        from_status=from_status, to_status=to_status, notes=notes,
    ))


# ─── Endpoints ─────────────────────────────────────────────────

@router.get("/", response_model=list[ServiceAccountListItem])
async def list_accounts(
    provider: str | None = None,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(
            Project.id,
            Project.name,
            Project.supply_source_id,
            Project.external_project_id,
            Project.status,
            Project.order_method,
            Project.created_at,
            SupplySource.provider,
            Supplier.name.label("supplier_name"),
        )
        .join(SupplySource, Project.supply_source_id == SupplySource.id)
        .join(Supplier, SupplySource.supplier_id == Supplier.id)
        .order_by(SupplySource.provider, Supplier.name, Project.name)
    )
    if provider:
        stmt = stmt.where(SupplySource.provider == provider)
    if status:
        stmt = stmt.where(Project.status == status)
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(stmt)).all()
    return [
        ServiceAccountListItem(
            id=r.id,
            name=r.name,
            supply_source_id=r.supply_source_id,
            supplier_name=r.supplier_name,
            provider=r.provider,
            external_project_id=r.external_project_id,
            status=r.status,
            order_method=r.order_method,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.post("/", response_model=ServiceAccountListItem, status_code=201)
async def create_account(body: ServiceAccountCreate, db: AsyncSession = Depends(get_db)):
    ss = await db.get(SupplySource, body.supply_source_id)
    if not ss:
        raise HTTPException(404, "货源不存在")
    cloud = ss.provider

    encrypted = encrypt_dict(body.secret_data) if body.secret_data else encrypt_dict({})
    ca = CloudAccount(name=f"{cloud}-{body.name}", provider=cloud, secret_data=encrypted)
    db.add(ca)
    await db.flush()

    ds = DataSource(
        name=f"ds-{body.name}", cloud_account_id=ca.id,
        config=_data_source_config_for_create(cloud, body.external_project_id),
        is_active=True,
    )
    db.add(ds)
    await db.flush()

    project = Project(
        name=body.name,
        external_project_id=body.external_project_id,
        supply_source_id=body.supply_source_id,
        data_source_id=ds.id,
        notes=body.notes,
        order_method=body.order_method,
        status="active",
    )
    db.add(project)
    await db.flush()

    _log(db, project, "created", from_status="", to_status="active")
    await db.commit()

    su = await db.get(Supplier, ss.supplier_id)
    return ServiceAccountListItem(
        id=project.id,
        name=project.name,
        supply_source_id=project.supply_source_id,
        supplier_name=su.name if su else "",
        provider=cloud,
        external_project_id=project.external_project_id,
        status=project.status,
        order_method=project.order_method,
        created_at=project.created_at,
    )


# ─── Delete (physical / hard delete) ─────────────────────────

async def _hard_delete(account_id: int, db: AsyncSession):
    """Permanently delete an account and cascade to CloudAccount + DataSource."""
    from sqlalchemy import delete as sql_delete
    from app.models.daily_summary import BillingDailySummary

    project = await db.get(Project, account_id)
    if not project:
        raise HTTPException(404, "Service account not found")

    ds_id = project.data_source_id

    if ds_id:
        await db.execute(
            sql_delete(BillingDailySummary).where(BillingDailySummary.data_source_id == ds_id)
        )
        await db.execute(
            sql_delete(BillingData).where(BillingData.data_source_id == ds_id)
        )

    await db.execute(
        sql_delete(ProjectAssignmentLog).where(ProjectAssignmentLog.project_id == account_id)
    )

    await db.delete(project)
    await db.flush()

    if ds_id:
        ds = await db.get(DataSource, ds_id)
        if ds:
            ca_id = ds.cloud_account_id
            await db.delete(ds)
            await db.flush()
            if ca_id:
                other_ds = await db.execute(
                    select(func.count()).select_from(DataSource)
                    .where(DataSource.cloud_account_id == ca_id)
                )
                if (other_ds.scalar() or 0) == 0:
                    ca = await db.get(CloudAccount, ca_id)
                    if ca:
                        await db.delete(ca)
                        await db.flush()

    await db.commit()


@router.delete("/hard/{account_id}", status_code=204)
async def hard_delete_account(account_id: int, db: AsyncSession = Depends(get_db)):
    await _hard_delete(account_id, db)


# ─── All Accounts Daily Costs (must be before /{account_id}) ──

class AccountDailyCostRow(BaseModel):
    account_id: int
    account_name: str
    provider: str
    external_project_id: str
    date: str
    product: str | None
    cost: float


@router.get("/daily-report", response_model=list[AccountDailyCostRow])
async def daily_report(
    start_date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end_date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    provider: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    sd = dt.date.fromisoformat(start_date)
    ed = dt.date.fromisoformat(end_date) + dt.timedelta(days=1)

    stmt = (
        select(
            Project.id.label("account_id"),
            Project.name.label("account_name"),
            SupplySource.provider.label("provider"),
            BillingData.project_id,
            BillingData.date,
            BillingData.product,
            func.sum(BillingData.cost).label("cost"),
        )
        .join(
            Project,
            func.trim(BillingData.project_id) == func.trim(Project.external_project_id),
        )
        .join(SupplySource, Project.supply_source_id == SupplySource.id)
        .where(
            BillingData.provider == SupplySource.provider,
            BillingData.date >= sd,
            BillingData.date < ed,
        )
        .group_by(
            Project.id,
            Project.name,
            SupplySource.provider,
            BillingData.project_id,
            BillingData.date,
            BillingData.product,
        )
        .order_by(BillingData.date, BillingData.project_id, BillingData.product)
    )
    if provider:
        stmt = stmt.where(SupplySource.provider == provider)

    rows = (await db.execute(stmt)).all()

    return [
        AccountDailyCostRow(
            account_id=r.account_id,
            account_name=r.account_name,
            provider=r.provider,
            external_project_id=r.project_id or "",
            date=str(r.date),
            product=r.product or "Unknown",
            cost=float(r.cost),
        )
        for r in rows
    ]


@router.get("/daily-report/export")
async def export_daily_report(
    start_date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end_date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    provider: str | None = Query(None),
    discount_pct: float | None = Query(
        None,
        ge=0,
        le=100,
        description="统一折扣百分比；传入时导出增加「折扣」「折后费用」列",
    ),
    db: AsyncSession = Depends(get_db),
):
    rows = await daily_report(start_date, end_date, provider, db)
    return _build_excel(rows, f"daily_report_{start_date}_{end_date}.xlsx", discount_pct=discount_pct)


@router.get("/{account_id}", response_model=ServiceAccountDetail)
async def get_account(account_id: int, db: AsyncSession = Depends(get_db)):
    row = (await db.execute(
        select(Project, DataSource, CloudAccount, SupplySource, Supplier)
        .join(SupplySource, Project.supply_source_id == SupplySource.id)
        .join(Supplier, SupplySource.supplier_id == Supplier.id)
        .outerjoin(DataSource, Project.data_source_id == DataSource.id)
        .outerjoin(CloudAccount, DataSource.cloud_account_id == CloudAccount.id)
        .where(Project.id == account_id)
    )).first()
    if not row:
        raise HTTPException(404, "Service account not found")
    project, ds, ca, ss, su = row

    secret_fields: list[str] = []
    if ca:
        try:
            secret_fields = list(decrypt_to_dict(ca.secret_data).keys())
        except Exception:
            secret_fields = ["(encrypted)"]

    logs = (await db.execute(
        select(ProjectAssignmentLog)
        .where(ProjectAssignmentLog.project_id == account_id)
        .order_by(ProjectAssignmentLog.created_at.desc())
    )).scalars().all()

    history = [HistoryItem(
        id=lg.id, action=lg.action,
        from_status=lg.from_status, to_status=lg.to_status,
        operator=lg.operator, notes=lg.notes, created_at=lg.created_at,
    ) for lg in logs]

    return ServiceAccountDetail(
        id=project.id,
        name=project.name,
        supply_source_id=project.supply_source_id,
        supplier_id=su.id,
        supplier_name=su.name,
        provider=ss.provider,
        external_project_id=project.external_project_id,
        status=project.status,
        notes=project.notes,
        order_method=project.order_method,
        secret_fields=secret_fields,
        created_at=project.created_at,
        history=history,
    )


@router.put("/{account_id}", response_model=ServiceAccountDetail)
async def update_account(account_id: int, body: ServiceAccountUpdate, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, account_id)
    if not project:
        raise HTTPException(404, "Service account not found")

    data = body.model_dump(exclude_unset=True)
    secret_data = data.pop("secret_data", None)
    new_supply_source_id = data.pop("supply_source_id", None)

    for k, v in data.items():
        if hasattr(project, k):
            setattr(project, k, v)
    await db.flush()

    if new_supply_source_id is not None and new_supply_source_id != project.supply_source_id:
        new_ss = await db.get(SupplySource, new_supply_source_id)
        if not new_ss:
            raise HTTPException(404, "货源不存在")
        ext = (data.get("external_project_id") if "external_project_id" in data else None) or project.external_project_id
        ext = str(ext).strip()
        dup = (
            await db.execute(
                select(Project.id).where(
                    Project.supply_source_id == new_supply_source_id,
                    Project.external_project_id == ext,
                    Project.id != project.id,
                )
            )
        ).scalar_one_or_none()
        if dup:
            raise HTTPException(409, "目标货源下已存在相同账号 ID")
        project.supply_source_id = new_supply_source_id
        await db.flush()
        if project.data_source_id:
            ds = await db.get(DataSource, project.data_source_id)
            if ds and ds.cloud_account_id:
                ca = await db.get(CloudAccount, ds.cloud_account_id)
                if ca:
                    ca.provider = new_ss.provider
                    ca.name = f"{new_ss.provider}-{project.name}"[:100]
                prov_new = new_ss.provider
                base_cfg = _data_source_config_for_create(prov_new, ext)
                old_cfg = dict(ds.config) if ds.config else {}
                merged = {**old_cfg, **base_cfg}
                ds.config = merged
                await db.flush()

    prov = await _cloud_provider(db, project)
    if project.data_source_id and prov == "azure" and "external_project_id" in data:
        ds = await db.get(DataSource, project.data_source_id)
        if ds:
            cfg = dict(ds.config) if ds.config else {}
            cfg["subscription_id"] = project.external_project_id.strip()
            cfg.setdefault("collect_mode", "subscription")
            cfg.setdefault("cost_metric", "ActualCost")
            ds.config = cfg
            await db.flush()

    if secret_data is not None and project.data_source_id:
        ds = await db.get(DataSource, project.data_source_id)
        if ds:
            ca = await db.get(CloudAccount, ds.cloud_account_id)
            if ca:
                ca.secret_data = encrypt_dict(secret_data)
                await db.flush()

    await db.commit()
    return await get_account(account_id, db)


@router.post("/{account_id}/suspend", response_model=ServiceAccountDetail)
async def suspend_account(account_id: int, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, account_id)
    if not project:
        raise HTTPException(404, "Service account not found")
    if project.status not in ("active", "standby"):
        raise HTTPException(400, f"Cannot suspend in '{project.status}' state")

    old_status = project.status
    project.status = "inactive"

    _log(db, project, "suspended", from_status=old_status, to_status="inactive")
    await db.commit()
    return await get_account(account_id, db)


@router.post("/{account_id}/activate", response_model=ServiceAccountDetail)
async def activate_account(account_id: int, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, account_id)
    if not project:
        raise HTTPException(404, "Service account not found")
    if project.status not in ("inactive", "standby"):
        raise HTTPException(400, f"Cannot activate in '{project.status}' state")

    old_status = project.status
    project.status = "active"
    _log(db, project, "activated", from_status=old_status, to_status="active")
    await db.commit()
    return await get_account(account_id, db)


@router.delete("/{account_id}", status_code=204)
async def delete_account(account_id: int, db: AsyncSession = Depends(get_db)):
    await _hard_delete(account_id, db)


@router.get("/{account_id}/costs", response_model=CostSummary)
async def get_costs(
    account_id: int,
    start_date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end_date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, account_id)
    if not project:
        raise HTTPException(404, "Service account not found")

    prov = await _cloud_provider(db, project)
    sd = dt.date.fromisoformat(start_date)
    ed = dt.date.fromisoformat(end_date) + dt.timedelta(days=1)

    res = await db.execute(
        select(
            BillingData.date,
            BillingData.product,
            func.sum(BillingData.cost).label("cost"),
            func.sum(BillingData.usage_quantity).label("usage_quantity"),
            func.max(BillingData.usage_unit).label("usage_unit"),
        )
        .where(
            func.trim(BillingData.project_id) == project.external_project_id.strip(),
            BillingData.provider == prov,
            BillingData.date >= sd,
            BillingData.date < ed,
        )
        .group_by(BillingData.date, BillingData.product)
        .order_by(BillingData.date, BillingData.product)
    )
    rows = res.all()

    total = 0.0
    total_usage = 0.0
    svc_cost: dict[str, float] = {}
    svc_usage: dict[str, float] = {}
    svc_unit: dict[str, str | None] = {}
    daily_map: dict[str, float] = {}
    daily_usage_map: dict[str, float] = {}
    daily_by_service: list[DailyServiceCost] = []

    for r in rows:
        cost = float(r.cost)
        uq = float(r.usage_quantity or 0)
        product = r.product or "Unknown"
        date_str = str(r.date)

        total += cost
        total_usage += uq
        svc_cost[product] = svc_cost.get(product, 0.0) + cost
        svc_usage[product] = svc_usage.get(product, 0.0) + uq
        if product not in svc_unit:
            svc_unit[product] = r.usage_unit
        daily_map[date_str] = daily_map.get(date_str, 0.0) + cost
        daily_usage_map[date_str] = daily_usage_map.get(date_str, 0.0) + uq
        daily_by_service.append(DailyServiceCost(
            date=date_str, service=product, cost=cost,
            usage_quantity=uq, usage_unit=r.usage_unit,
        ))

    services = sorted(
        [CostByService(service=k, cost=v, usage_quantity=svc_usage[k], usage_unit=svc_unit.get(k))
         for k, v in svc_cost.items()],
        key=lambda x: x.cost, reverse=True,
    )
    daily = [DailyCost(date=k, cost=v, usage_quantity=daily_usage_map[k])
             for k, v in sorted(daily_map.items())]

    return CostSummary(
        total_cost=total, total_usage=total_usage,
        services=services, daily=daily, daily_by_service=daily_by_service,
    )


@router.get("/{account_id}/credentials")
async def get_credentials(account_id: int, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, account_id)
    if not project:
        raise HTTPException(404, "Service account not found")
    if not project.data_source_id:
        return {}
    ds = await db.get(DataSource, project.data_source_id)
    if not ds:
        return {}
    ca = await db.get(CloudAccount, ds.cloud_account_id)
    if not ca:
        return {}
    try:
        return decrypt_to_dict(ca.secret_data)
    except Exception:
        raise HTTPException(500, "Failed to decrypt credentials")


@router.get("/{account_id}/costs/export")
async def export_account_costs(
    account_id: int,
    start_date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end_date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    discount_pct: float | None = Query(
        None,
        ge=0,
        le=100,
        description="统一折扣百分比；传入时导出增加「折扣」「折后费用」列",
    ),
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, account_id)
    if not project:
        raise HTTPException(404, "Service account not found")

    prov = await _cloud_provider(db, project)
    sd = dt.date.fromisoformat(start_date)
    ed = dt.date.fromisoformat(end_date) + dt.timedelta(days=1)

    billing_stmt = (
        select(
            BillingData.date,
            BillingData.product,
            BillingData.usage_type,
            BillingData.region,
            BillingData.cost,
            BillingData.usage_quantity,
            BillingData.usage_unit,
        )
        .where(
            func.trim(BillingData.project_id) == project.external_project_id.strip(),
            BillingData.provider == prov,
            BillingData.date >= sd,
            BillingData.date < ed,
        )
        .order_by(BillingData.date, BillingData.product)
    )
    rows = (await db.execute(billing_stmt)).all()

    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    wb = Workbook()
    ws = wb.active
    ws.title = "费用明细"

    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    header_align = Alignment(horizontal="center")

    if discount_pct is not None:
        factor = 1.0 - float(discount_pct) / 100.0
        headers = [
            "日期",
            "服务",
            "用量类型",
            "区域",
            "费用(USD)",
            "折扣(%)",
            "折后费用(USD)",
            "用量",
            "用量单位",
        ]
    else:
        factor = 1.0
        headers = ["日期", "服务", "用量类型", "区域", "费用(USD)", "用量", "用量单位"]

    for ci, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=ci, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align

    for ri, r in enumerate(rows, 2):
        cost = float(r.cost)
        ws.cell(row=ri, column=1, value=str(r.date))
        ws.cell(row=ri, column=2, value=r.product or "Unknown")
        ws.cell(row=ri, column=3, value=r.usage_type or "")
        ws.cell(row=ri, column=4, value=r.region or "")
        ws.cell(row=ri, column=5, value=cost).number_format = '#,##0.000000'
        if discount_pct is not None:
            ws.cell(row=ri, column=6, value=float(discount_pct))
            ws.cell(row=ri, column=7, value=cost * factor).number_format = '#,##0.000000'
            ws.cell(row=ri, column=8, value=float(r.usage_quantity) if r.usage_quantity else 0)
            ws.cell(row=ri, column=9, value=r.usage_unit or "")
        else:
            ws.cell(row=ri, column=6, value=float(r.usage_quantity) if r.usage_quantity else 0)
            ws.cell(row=ri, column=7, value=r.usage_unit or "")

    for col in range(1, len(headers) + 1):
        ws.column_dimensions[chr(64 + col)].width = 18

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    fname = f"{project.name}_{start_date}_{end_date}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


def _build_excel(
    rows: list[AccountDailyCostRow],
    filename: str,
    discount_pct: float | None = None,
):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    wb = Workbook()
    ws = wb.active
    ws.title = "日报表"

    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    header_align = Alignment(horizontal="center")

    if discount_pct is not None:
        factor = 1.0 - float(discount_pct) / 100.0
        headers = [
            "云厂商",
            "账号名称",
            "账号ID",
            "日期",
            "服务",
            "费用(USD)",
            "折扣(%)",
            "折后费用(USD)",
        ]
    else:
        factor = 1.0
        headers = ["云厂商", "账号名称", "账号ID", "日期", "服务", "费用(USD)"]

    for ci, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=ci, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align

    for ri, r in enumerate(rows, 2):
        cost = float(r.cost)
        ws.cell(row=ri, column=1, value=r.provider.upper())
        ws.cell(row=ri, column=2, value=r.account_name)
        ws.cell(row=ri, column=3, value=r.external_project_id)
        ws.cell(row=ri, column=4, value=r.date)
        ws.cell(row=ri, column=5, value=r.product or "Unknown")
        ws.cell(row=ri, column=6, value=cost).number_format = '#,##0.000000'
        if discount_pct is not None:
            ws.cell(row=ri, column=7, value=float(discount_pct))
            ws.cell(row=ri, column=8, value=cost * factor).number_format = '#,##0.000000'

    for col in range(1, len(headers) + 1):
        ws.column_dimensions[chr(64 + col)].width = 18

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/discover-gcp-projects")
async def discover_gcp_projects(db: AsyncSession = Depends(get_db)):
    """为账单中存在但未建档的 GCP project 创建 Project，挂在系统供应商「未分配资源组」的 GCP 货源下。"""
    billing_res = await db.execute(
        select(
            BillingData.project_id,
            func.max(BillingData.project_name).label("project_name"),
        )
        .where(BillingData.provider == "gcp")
        .group_by(BillingData.project_id)
    )
    billing_projects = {r.project_id: r.project_name for r in billing_res.all() if r.project_id}

    if not billing_projects:
        return {"created": 0, "projects": []}

    ss_id, _ = await ensure_other_gcp_supply_source_id(db)

    existing_res = await db.execute(
        select(Project.external_project_id)
        .join(SupplySource, Project.supply_source_id == SupplySource.id)
        .where(SupplySource.provider == "gcp", Project.external_project_id.in_(list(billing_projects.keys())))
    )
    existing = {r[0] for r in existing_res.all()}

    created = []
    for pid, pname in billing_projects.items():
        if pid in existing:
            continue
        project = Project(
            name=pname or pid,
            external_project_id=pid,
            supply_source_id=ss_id,
            status="standby",
        )
        db.add(project)
        created.append(pid)

    if created:
        await db.commit()

    return {"created": len(created), "projects": created}
