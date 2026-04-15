"""Resource inventory API."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.resource import ResourceInventory
from app.schemas.billing import ResourceRead

router = APIRouter()


@router.get("/", response_model=list[ResourceRead])
async def list_resources(
    provider: str | None = None,
    project_id: str | None = None,
    resource_type: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(ResourceInventory).order_by(ResourceInventory.id)
    if provider:
        stmt = stmt.where(ResourceInventory.provider == provider)
    if project_id:
        stmt = stmt.where(ResourceInventory.project_id == project_id)
    if resource_type:
        stmt = stmt.where(ResourceInventory.resource_type == resource_type)
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/{resource_id}", response_model=ResourceRead)
async def get_resource(resource_id: int, db: AsyncSession = Depends(get_db)):
    resource = await db.get(ResourceInventory, resource_id)
    if not resource:
        raise HTTPException(404, "Resource not found")
    return resource
