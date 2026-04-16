"""Admin: user list & disable toggle, cloud-account grants.

Only `cloud_admin` may access. Creating users is not exposed — users are
auto-provisioned on first Casdoor login.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_roles
from app.auth.principal import Principal
from app.database import get_db
from app.models.user import User
from app.models.user_grant import UserCloudAccountGrant
from app.schemas.auth import GrantCreate, GrantRead, UserRead
from app.services.audit_service import log_operation


router = APIRouter()


@router.get("/", response_model=list[UserRead])
async def list_users(
    db: AsyncSession = Depends(get_db),
    _: Principal = Depends(require_roles("cloud_admin")),
):
    rows = await db.execute(select(User).order_by(User.id))
    return rows.scalars().all()


@router.patch("/{user_id}/active", response_model=UserRead)
async def toggle_active(
    user_id: int,
    is_active: bool,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_roles("cloud_admin")),
):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    before = {"is_active": user.is_active}
    user.is_active = is_active
    await log_operation(
        db, action="toggle_user_active", target_type="user", target_id=user_id,
        before_data=before, after_data={"is_active": is_active},
        operator=principal.user.username,
    )
    return user


# ---- Grants ----

@router.get("/{user_id}/cloud-account-grants", response_model=list[GrantRead])
async def list_grants(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    _: Principal = Depends(require_roles("cloud_admin")),
):
    rows = await db.execute(
        select(UserCloudAccountGrant).where(UserCloudAccountGrant.user_id == user_id)
    )
    return rows.scalars().all()


@router.post("/{user_id}/cloud-account-grants", response_model=GrantRead, status_code=201)
async def add_grant(
    user_id: int,
    body: GrantCreate,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_roles("cloud_admin")),
):
    if body.user_id != user_id:
        raise HTTPException(400, "user_id mismatch")
    grant = UserCloudAccountGrant(
        user_id=user_id,
        cloud_account_id=body.cloud_account_id,
        scope=body.scope,
        granted_by=principal.user.id,
    )
    db.add(grant)
    await db.flush()
    await log_operation(
        db, action="grant_cloud_account", target_type="user_grant", target_id=grant.id,
        after_data={"user_id": user_id, "cloud_account_id": body.cloud_account_id, "scope": body.scope},
        operator=principal.user.username,
    )
    return grant


@router.delete("/{user_id}/cloud-account-grants/{grant_id}", status_code=204)
async def revoke_grant(
    user_id: int,
    grant_id: int,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_roles("cloud_admin")),
):
    grant = await db.get(UserCloudAccountGrant, grant_id)
    if not grant or grant.user_id != user_id:
        raise HTTPException(404, "Grant not found")
    await db.delete(grant)
    await log_operation(
        db, action="revoke_cloud_account_grant", target_type="user_grant", target_id=grant_id,
        before_data={"user_id": user_id, "cloud_account_id": grant.cloud_account_id},
        operator=principal.user.username,
    )
    return None
