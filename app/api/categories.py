"""Categories CRUD API."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_roles
from app.database import get_db
from app.models.category import Category
from app.schemas.category import CategoryCreate, CategoryUpdate, CategoryRead
from app.services.audit_service import log_operation

router = APIRouter(dependencies=[Depends(require_roles("cloud_admin"))])


@router.get("/", response_model=list[CategoryRead])
async def list_categories(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Category).order_by(Category.id))
    return result.scalars().all()


@router.post("/", response_model=CategoryRead, status_code=201)
async def create_category(body: CategoryCreate, db: AsyncSession = Depends(get_db)):
    cat = Category(**body.model_dump())
    db.add(cat)
    await db.commit()
    await db.refresh(cat)
    return cat


@router.get("/{category_id}", response_model=CategoryRead)
async def get_category(category_id: int, db: AsyncSession = Depends(get_db)):
    cat = await db.get(Category, category_id)
    if not cat:
        raise HTTPException(404, "Category not found")
    return cat


@router.put("/{category_id}", response_model=CategoryRead)
async def update_category(category_id: int, body: CategoryUpdate, db: AsyncSession = Depends(get_db)):
    cat = await db.get(Category, category_id)
    if not cat:
        raise HTTPException(404, "Category not found")
    changes = body.model_dump(exclude_unset=True)
    if "markup_rate" in changes and changes["markup_rate"] != cat.markup_rate:
        await log_operation(db, action="update_markup_rate", target_type="category", target_id=category_id,
                            before_data={"markup_rate": float(cat.markup_rate)},
                            after_data={"markup_rate": float(changes["markup_rate"])})
    for k, v in changes.items():
        setattr(cat, k, v)
    await db.commit()
    await db.refresh(cat)
    return cat


@router.delete("/{category_id}", status_code=204)
async def delete_category(category_id: int, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import func
    from app.models.data_source import DataSource
    from app.models.project import Project
    from app.models.monthly_bill import MonthlyBill

    cat = await db.get(Category, category_id)
    if not cat:
        raise HTTPException(404, "Category not found")
    ds_count = (await db.execute(
        select(func.count()).select_from(DataSource).where(DataSource.category_id == category_id)
    )).scalar() or 0
    proj_count = (await db.execute(
        select(func.count()).select_from(Project).where(Project.category_id == category_id)
    )).scalar() or 0
    bill_count = (await db.execute(
        select(func.count()).select_from(MonthlyBill).where(MonthlyBill.category_id == category_id)
    )).scalar() or 0
    total = ds_count + proj_count + bill_count
    if total > 0:
        raise HTTPException(
            400,
            f"Cannot delete: referenced by {ds_count} data source(s), {proj_count} project(s), {bill_count} bill(s)",
        )
    await db.delete(cat)
    await db.commit()
