"""Data Sources CRUD API."""

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_principal
from app.auth.principal import Principal
from app.auth.scope import (
    ensure_cloud_account_visible,
    ensure_data_source_visible,
    has_full_access,
    visible_data_source_ids,
)
from app.database import get_db
from app.models.cloud_account import CloudAccount
from app.models.data_source import DataSource
from app.schemas.data_source import DataSourceCreate, DataSourceUpdate, DataSourceRead
from app.services.crypto_service import decrypt_to_dict

# router 级"是否云管角色"由 main.py 的 _cloud("data_sources") 完成。
# 数据范围:每个 data_source 绑定到一个 cloud_account,按 cloud_account.provider 限定。
router = APIRouter()


@router.get("/", response_model=list[DataSourceRead])
async def list_data_sources(
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    stmt = select(DataSource).order_by(DataSource.id)
    if not has_full_access(principal):
        visible = await visible_data_source_ids(db, principal)
        if not visible:
            return []
        stmt = stmt.where(DataSource.id.in_(visible))
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/", response_model=DataSourceRead, status_code=201)
async def create_data_source(
    body: DataSourceCreate,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    # 数据范围:body.cloud_account_id 必须在用户范围
    await ensure_cloud_account_visible(db, principal, body.cloud_account_id)
    ds = DataSource(**body.model_dump())
    db.add(ds)
    await db.commit()
    await db.refresh(ds)
    return ds


# ── GCP 账单视图接入：验证读取 + 建源（供前端弹窗调用）──────────────────
# 供应商把某个客户项目的账单做成一个 BigQuery 视图，授权我们的 SA 只读，
# 我们建一条 DataSource 指向该视图即可。字段名默认对齐 detailed 导出。

class GcpViewSpec(BaseModel):
    cloud_account_id: int | None = None  # 不传则自动选唯一活跃 GCP 云账号
    project_id: str
    dataset: str
    table: str
    cost_field: str = "cost"
    usage_field: str = "amount_in_pricing_units"


class GcpViewCreate(GcpViewSpec):
    name: str
    category_id: int | None = None


async def _resolve_gcp_account(
    db: AsyncSession, principal: Principal, cloud_account_id: int | None
) -> CloudAccount:
    """给定 id 则校验可见 + provider；不给则自动选唯一活跃 GCP 云账号。"""
    if cloud_account_id is None:
        ca = (await db.execute(
            select(CloudAccount).where(
                CloudAccount.provider == "gcp",
                CloudAccount.is_active.is_(True),
            )
        )).scalars().all()
        if not ca:
            raise HTTPException(400, "没有可用的 GCP 云账号")
        if len(ca) > 1:
            ids = ", ".join(f"#{c.id}({c.name})" for c in ca)
            raise HTTPException(400, f"有多个 GCP 云账号，请指定 cloud_account_id：{ids}")
        account = ca[0]
    else:
        account = await db.get(CloudAccount, cloud_account_id)
        if not account:
            raise HTTPException(404, "Cloud account not found")
        if account.provider != "gcp":
            raise HTTPException(400, f"Cloud account #{cloud_account_id} 不是 gcp（是 {account.provider}）")

    # 数据范围：账号必须在用户可见范围内
    await ensure_cloud_account_visible(db, principal, account.id)
    return account


def _build_gcp_view_config(spec: GcpViewSpec) -> dict:
    return {
        "project_id": spec.project_id.strip(),
        "dataset": spec.dataset.strip(),
        "table": spec.table.strip(),
        "cost_field": spec.cost_field.strip() or "cost",
        "usage_field": spec.usage_field.strip() or "amount_in_pricing_units",
        "is_native": True,
    }


@router.post("/gcp-view/verify")
async def verify_gcp_view(
    body: GcpViewSpec,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    """只读试查：用该云账号的 SA 读这个视图最近 60 天，回报能不能读、有哪些项目/行数。

    存源之前先跑这个，能挡住白名单 project.id 拼错、视图空、SA 未授权等坑。
    """
    ca = await _resolve_gcp_account(db, principal, body.cloud_account_id)
    secret = decrypt_to_dict(ca.secret_data)
    config = _build_gcp_view_config(body)

    end = dt.date.today()
    start = end - dt.timedelta(days=60)
    from app.collectors import get_collector  # lazy：避免把 google 库加进 web 启动路径
    collector = get_collector("gcp")
    try:
        rows = await run_in_threadpool(
            collector.collect_billing, secret, config,
            start.isoformat(), end.isoformat(),
        )
    except Exception as e:  # 权限/拼写/字段错误统一回给前端，不抛 500
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:400]}",
                "rows": 0, "projects": [], "window": f"{start} ~ {end}"}

    agg: dict[str, dict] = {}
    for r in rows:
        pid = r.get("project_id") or ""
        a = agg.setdefault(pid, {"project_id": pid, "project_name": r.get("project_name") or pid,
                                 "cost": 0.0, "rows": 0, "min_date": r["date"], "max_date": r["date"]})
        a["cost"] += float(r.get("cost") or 0)
        a["rows"] += 1
        a["min_date"] = min(a["min_date"], r["date"])
        a["max_date"] = max(a["max_date"], r["date"])
    projects = sorted(agg.values(), key=lambda x: -x["cost"])
    for p in projects:
        p["cost"] = round(p["cost"], 4)

    return {"ok": True, "rows": len(rows), "projects": projects,
            "window": f"{start} ~ {end}",
            "note": "0 行=视图能读但无数据（可能白名单拼错或刚开导出未回填）" if not rows else ""}


@router.post("/gcp-view", response_model=DataSourceRead, status_code=201)
async def create_gcp_view(
    body: GcpViewCreate,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    """建一条指向 GCP 账单视图的 DataSource（字段名默认对齐 detailed 导出）。

    建完前端再调 POST /api/sync/{id} 触发同步；同步时 auto_create_gcp_projects
    会自动把视图里的 project.id 建成项目。
    """
    ca = await _resolve_gcp_account(db, principal, body.cloud_account_id)
    config = _build_gcp_view_config(body)

    # 幂等：同一云账号下已存在指向同一 project/dataset/table 的源则拒绝，避免重复
    existing = (await db.execute(
        select(DataSource).where(DataSource.cloud_account_id == ca.id)
    )).scalars().all()
    for ds in existing:
        c = ds.config or {}
        if (c.get("project_id"), c.get("dataset"), c.get("table")) == (
            config["project_id"], config["dataset"], config["table"]
        ):
            raise HTTPException(409, f"已存在指向该视图的数据源 ds#{ds.id}（name={ds.name}）")

    ds = DataSource(
        name=body.name.strip(),
        cloud_account_id=ca.id,
        category_id=body.category_id,
        config=config,
        is_active=True,
    )
    db.add(ds)
    await db.commit()
    await db.refresh(ds)
    return ds


@router.get("/{ds_id}", response_model=DataSourceRead)
async def get_data_source(
    ds_id: int,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    await ensure_data_source_visible(db, principal, ds_id)
    ds = await db.get(DataSource, ds_id)
    if not ds:
        raise HTTPException(404, "Data source not found")
    return ds


@router.put("/{ds_id}", response_model=DataSourceRead)
async def update_data_source(
    ds_id: int,
    body: DataSourceUpdate,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    await ensure_data_source_visible(db, principal, ds_id)
    ds = await db.get(DataSource, ds_id)
    if not ds:
        raise HTTPException(404, "Data source not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(ds, k, v)
    await db.commit()
    await db.refresh(ds)
    return ds


@router.delete("/{ds_id}", status_code=204)
async def delete_data_source(
    ds_id: int,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    from sqlalchemy import func
    from app.models.billing import BillingData
    from app.models.project import Project

    await ensure_data_source_visible(db, principal, ds_id)
    ds = await db.get(DataSource, ds_id)
    if not ds:
        raise HTTPException(404, "Data source not found")
    billing_count = (await db.execute(
        select(func.count()).select_from(BillingData).where(BillingData.data_source_id == ds_id)
    )).scalar() or 0
    project_count = (await db.execute(
        select(func.count()).select_from(Project).where(Project.data_source_id == ds_id)
    )).scalar() or 0
    if billing_count > 0 or project_count > 0:
        raise HTTPException(
            400,
            f"Cannot delete: {billing_count} billing record(s) and {project_count} project(s) still reference this data source",
        )
    await db.delete(ds)
    await db.commit()
