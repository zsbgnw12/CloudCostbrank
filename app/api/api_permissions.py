"""Site-wide module switches (admin only)."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_roles
from app.auth.principal import Principal
from app.database import get_db
from app.models.api_module_permission import ApiModulePermission
from app.schemas.auth import ModulePermissionRead, ModulePermissionToggle
from app.services.audit_service import log_operation


router = APIRouter()


@router.get("/", response_model=list[ModulePermissionRead])
async def list_modules(
    db: AsyncSession = Depends(get_db),
    _: Principal = Depends(require_roles("cloud_admin")),
):
    rows = await db.execute(select(ApiModulePermission).order_by(ApiModulePermission.module))
    return rows.scalars().all()


@router.patch("/{module}", response_model=ModulePermissionRead)
async def toggle_module(
    module: str,
    body: ModulePermissionToggle,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_roles("cloud_admin")),
):
    row = await db.execute(select(ApiModulePermission).where(ApiModulePermission.module == module))
    perm = row.scalar_one_or_none()
    if not perm:
        raise HTTPException(404, f"Unknown module '{module}'")
    before = {"enabled": perm.enabled}
    perm.enabled = body.enabled
    perm.updated_by = principal.user.id
    perm.updated_at = datetime.now(timezone.utc)
    await log_operation(
        db, action="toggle_module", target_type="api_module_permission", target_id=module,
        before_data=before, after_data={"enabled": body.enabled},
        operator=principal.user.username,
    )
    return perm
