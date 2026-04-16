"""Cloud Accounts CRUD API."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_current_principal, require_roles
from app.auth.principal import Principal
from app.auth.scope import ensure_cloud_account_visible, visible_cloud_account_ids
from app.database import get_db
from app.models.cloud_account import CloudAccount
from app.schemas.cloud_account import CloudAccountCreate, CloudAccountUpdate, CloudAccountRead
from app.services.crypto_service import encrypt_dict
from app.services.audit_service import log_operation

router = APIRouter()


@router.get("/", response_model=list[CloudAccountRead])
async def list_cloud_accounts(
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    visible = await visible_cloud_account_ids(db, principal)
    stmt = select(CloudAccount).order_by(CloudAccount.id)
    if visible is not None:
        if not visible:
            return []
        stmt = stmt.where(CloudAccount.id.in_(visible))
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/", response_model=CloudAccountRead, status_code=201,
             dependencies=[Depends(require_roles("cloud_admin"))])
async def create_cloud_account(
    body: CloudAccountCreate,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    encrypted = encrypt_dict(body.secret_data)
    account = CloudAccount(
        name=body.name,
        provider=body.provider,
        secret_data=encrypted,
    )
    db.add(account)
    await db.flush()
    await db.refresh(account)
    await log_operation(
        db, action="create_cloud_account", target_type="cloud_account", target_id=account.id,
        after_data={"name": account.name, "provider": account.provider},
        user_id=principal.user.id, operator=principal.user.username,
    )
    await db.commit()
    return account


@router.get("/{account_id}", response_model=CloudAccountRead)
async def get_cloud_account(
    account_id: int,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    await ensure_cloud_account_visible(db, principal, account_id)
    account = await db.get(CloudAccount, account_id)
    if not account:
        raise HTTPException(404, "Cloud account not found")
    return account


@router.put("/{account_id}", response_model=CloudAccountRead,
            dependencies=[Depends(require_roles("cloud_admin"))])
async def update_cloud_account(
    account_id: int,
    body: CloudAccountUpdate,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    account = await db.get(CloudAccount, account_id)
    if not account:
        raise HTTPException(404, "Cloud account not found")
    data = body.model_dump(exclude_unset=True)
    before = {"name": account.name, "provider": account.provider, "is_active": account.is_active}
    if "secret_data" in data and data["secret_data"] is not None:
        data["secret_data"] = encrypt_dict(data["secret_data"])
    for k, v in data.items():
        setattr(account, k, v)
    await log_operation(
        db, action="update_cloud_account", target_type="cloud_account", target_id=account_id,
        before_data=before,
        after_data={k: v for k, v in data.items() if k != "secret_data"},
        user_id=principal.user.id, operator=principal.user.username,
    )
    await db.commit()
    await db.refresh(account)
    return account


@router.delete("/{account_id}", status_code=204,
               dependencies=[Depends(require_roles("cloud_admin"))])
async def delete_cloud_account(
    account_id: int,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    account = await db.get(CloudAccount, account_id)
    if not account:
        raise HTTPException(404, "Cloud account not found")
    from app.models.data_source import DataSource
    dep_result = await db.execute(
        select(func.count()).select_from(DataSource).where(DataSource.cloud_account_id == account_id)
    )
    dep_count = dep_result.scalar() or 0
    if dep_count > 0:
        raise HTTPException(400, f"Cannot delete: {dep_count} data source(s) still reference this cloud account")
    await log_operation(
        db, action="delete_cloud_account", target_type="cloud_account", target_id=account_id,
        before_data={"name": account.name, "provider": account.provider},
        user_id=principal.user.id, operator=principal.user.username,
    )
    await db.delete(account)
    await db.commit()
