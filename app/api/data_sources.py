"""Data Sources CRUD API."""

from fastapi import APIRouter, Depends, HTTPException
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
