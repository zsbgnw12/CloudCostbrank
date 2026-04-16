"""Resource inventory API."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_principal
from app.auth.principal import Principal
from app.auth.scope import visible_data_source_ids
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
    principal: Principal = Depends(get_current_principal),
):
    visible_ds = await visible_data_source_ids(db, principal)
    stmt = select(ResourceInventory).order_by(ResourceInventory.id)
    if visible_ds is not None:
        if not visible_ds:
            return []
        stmt = stmt.where(ResourceInventory.data_source_id.in_(visible_ds))
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
async def get_resource(
    resource_id: int,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    resource = await db.get(ResourceInventory, resource_id)
    if not resource:
        raise HTTPException(404, "Resource not found")
    visible_ds = await visible_data_source_ids(db, principal)
    if visible_ds is not None and resource.data_source_id not in visible_ds:
        raise HTTPException(403, "Resource out of scope")
    return resource
