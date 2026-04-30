"""API key management.

Create/list/revoke keys. On create the plaintext is returned exactly once.
Only `cloud_admin` may issue keys; any user may list / revoke their own.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.api_key_service import generate_key
from app.auth.dependencies import get_current_principal, require_roles
from app.auth.principal import Principal
from app.database import get_db
from app.models.api_key import ApiKey
from app.schemas.auth import ApiKeyCreate, ApiKeyCreated, ApiKeyRead
from app.services.audit_service import log_operation


router = APIRouter()


@router.get("/", response_model=list[ApiKeyRead])
async def list_keys(
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    # Admin sees all; others only their own.
    stmt = select(ApiKey).order_by(ApiKey.id.desc())
    if not principal.is_admin:
        stmt = stmt.where(ApiKey.owner_user_id == principal.user.id)
    rows = await db.execute(stmt)
    return rows.scalars().all()


@router.post("/", response_model=ApiKeyCreated, status_code=201)
async def create_key(
    body: ApiKeyCreate,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_roles("cloud_admin")),
):
    owner_id = body.owner_user_id or principal.user.id
    plaintext, key_hash, key_prefix = generate_key()
    ak = ApiKey(
        name=body.name,
        key_hash=key_hash,
        key_prefix=key_prefix,
        owner_user_id=owner_id,
        allowed_modules=body.allowed_modules,
        allowed_cloud_account_ids=body.allowed_cloud_account_ids,
        expires_at=body.expires_at,
    )
    db.add(ak)
    await db.flush()
    await log_operation(
        db, action="create_api_key", target_type="api_key", target_id=ak.id,
        after_data={"name": body.name, "owner_user_id": owner_id,
                    "allowed_modules": body.allowed_modules,
                    "allowed_cloud_account_ids": body.allowed_cloud_account_ids},
        operator=principal.user.username,
    )

    data = ApiKeyRead.model_validate(ak, from_attributes=True).model_dump()
    return ApiKeyCreated(**data, plaintext_key=plaintext)


@router.delete("/{key_id}", status_code=204)
async def revoke_key(
    key_id: int,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    ak = await db.get(ApiKey, key_id)
    if not ak:
        raise HTTPException(404, "Key not found")
    if not principal.is_admin and ak.owner_user_id != principal.user.id:
        raise HTTPException(403, "Forbidden")
    if ak.revoked_at is not None:
        return None
    ak.revoked_at = datetime.now(timezone.utc)
    await log_operation(
        db, action="revoke_api_key", target_type="api_key", target_id=key_id,
        operator=principal.user.username,
    )
    return None
