"""Data-scope helpers.

`visible_cloud_account_ids(user)` returns:
  - None  → full access (cloud_admin)
  - list  → explicit whitelist; may be [] meaning "nothing visible"

All list/detail endpoints must call this before building their query, then
apply `WHERE cloud_account_id IN (...)` or return [] when the list is empty.
"""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.principal import Principal
from app.models.user_grant import UserCloudAccountGrant


async def visible_cloud_account_ids(
    db: AsyncSession,
    principal: Principal,
) -> list[int] | None:
    """Returns the visible cloud account ids for this principal.

    Rules:
      - Admin principal (JWT or API-key owner) with NO `restricted_cloud_account_ids`
        → None (full access).
      - Admin API-key WITH explicit `restricted_cloud_account_ids`
        → the explicit list verbatim (no intersection with grants).
      - Non-admin user → intersection of grants and (optional) key restriction.
    """
    user = principal.user
    # 只信 principal.roles(middleware 已按认证方式正确填充)。
    is_admin = "cloud_admin" in (principal.roles or [])
    restricted = principal.restricted_cloud_account_ids if principal.method.value == "api_key" else None

    if is_admin:
        if restricted is None:
            return None
        return sorted(set(restricted))

    stmt = select(UserCloudAccountGrant.cloud_account_id).where(
        UserCloudAccountGrant.user_id == user.id
    )
    base: set[int] = set((await db.execute(stmt)).scalars().all())
    if restricted is not None:
        base &= set(restricted)
    return sorted(base)


async def visible_data_source_ids(
    db: AsyncSession,
    principal: Principal,
) -> list[int] | None:
    """Translate visible cloud accounts into their data-source ids.

    Returns None for full-access admins; empty list means "no visibility".
    """
    from app.models.data_source import DataSource  # local import to avoid cycles

    account_ids = await visible_cloud_account_ids(db, principal)
    if account_ids is None:
        return None
    if not account_ids:
        return []
    rows = (
        await db.execute(select(DataSource.id).where(DataSource.cloud_account_id.in_(account_ids)))
    ).scalars().all()
    return list(rows)


async def ensure_cloud_account_visible(
    db: AsyncSession,
    principal: Principal,
    cloud_account_id: int,
) -> None:
    """Raise 403 if the given cloud_account is not in the principal's scope."""
    visible = await visible_cloud_account_ids(db, principal)
    if visible is None:
        return
    if cloud_account_id not in visible:
        raise HTTPException(status_code=403, detail="Cloud account out of scope")
