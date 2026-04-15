"""Data Sources CRUD API."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.data_source import DataSource
from app.schemas.data_source import DataSourceCreate, DataSourceUpdate, DataSourceRead

router = APIRouter()


@router.get("/", response_model=list[DataSourceRead])
async def list_data_sources(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DataSource).order_by(DataSource.id))
    return result.scalars().all()


@router.post("/", response_model=DataSourceRead, status_code=201)
async def create_data_source(body: DataSourceCreate, db: AsyncSession = Depends(get_db)):
    ds = DataSource(**body.model_dump())
    db.add(ds)
    await db.commit()
    await db.refresh(ds)
    return ds


@router.get("/{ds_id}", response_model=DataSourceRead)
async def get_data_source(ds_id: int, db: AsyncSession = Depends(get_db)):
    ds = await db.get(DataSource, ds_id)
    if not ds:
        raise HTTPException(404, "Data source not found")
    return ds


@router.put("/{ds_id}", response_model=DataSourceRead)
async def update_data_source(ds_id: int, body: DataSourceUpdate, db: AsyncSession = Depends(get_db)):
    ds = await db.get(DataSource, ds_id)
    if not ds:
        raise HTTPException(404, "Data source not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(ds, k, v)
    await db.commit()
    await db.refresh(ds)
    return ds


@router.delete("/{ds_id}", status_code=204)
async def delete_data_source(ds_id: int, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import func
    from app.models.billing import BillingData
    from app.models.project import Project

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
