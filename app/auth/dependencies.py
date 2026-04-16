"""FastAPI dependencies for auth & module gating."""

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.principal import Principal
from app.config import settings
from app.database import get_db
from app.models.api_module_permission import ApiModulePermission
from app.models.user import User


def _anonymous_allowed(path: str) -> bool:
    if not settings.AUTH_ENFORCED:
        return True
    for prefix in (settings.AUTH_ANONYMOUS_PREFIXES or "").split(","):
        prefix = prefix.strip()
        if prefix and path.startswith(prefix):
            return True
    return False


def get_current_principal(request: Request) -> Principal:
    principal: Principal | None = getattr(request.state, "principal", None)
    if principal is not None:
        return principal
    if _anonymous_allowed(request.url.path):
        # Synthetic anonymous principal for gray-release / public endpoints.
        raise HTTPException(status_code=401, detail="Anonymous not permitted here")
    raise HTTPException(status_code=401, detail="Unauthorized")


def get_current_user(principal: Principal = Depends(get_current_principal)) -> User:
    return principal.user


def require_roles(*roles: str):
    """Dependency factory: allow request only if principal has ANY of `roles`.

    `cloud_admin` is always allowed (super-role) unless explicitly excluded
    by passing the role list without it.
    """
    allowed = set(roles)
    # Admin implicitly allowed everywhere guarded by require_roles.
    allowed.add("cloud_admin")

    def _dep(principal: Principal = Depends(get_current_principal)) -> Principal:
        effective = set(principal.roles or []) | set(principal.user.roles or [])
        if effective & allowed:
            return principal
        raise HTTPException(status_code=403, detail="Forbidden: missing required role")

    return _dep


def require_module(module: str):
    """Dependency: reject if the module is globally disabled, OR if this
    principal is an API key that doesn't list this module in `allowed_modules`.
    """

    async def _dep(
        principal: Principal = Depends(get_current_principal),
        db: AsyncSession = Depends(get_db),
    ) -> Principal:
        # API key scope check first (cheap, in-memory).
        if principal.restricted_modules is not None:
            if module not in principal.restricted_modules:
                raise HTTPException(
                    status_code=403, detail=f"API key not permitted for module '{module}'"
                )

        # Global switch.
        row = await db.execute(
            select(ApiModulePermission).where(ApiModulePermission.module == module)
        )
        perm = row.scalar_one_or_none()
        if perm is not None and not perm.enabled:
            raise HTTPException(status_code=403, detail=f"Module '{module}' is disabled")
        return principal

    return _dep
