"""Upsert local User from Casdoor identity claims."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


async def upsert_from_casdoor(
    db: AsyncSession,
    *,
    claims: dict,
    roles: list[str],
    ip: str | None = None,
) -> User:
    """Create or refresh the local User shadow for a Casdoor identity.

    `claims` may be either the id_token payload or the userinfo response — we
    read `sub`, `name`/`preferred_username`, `email`, `displayName`, `avatar`.
    """
    sub = str(claims.get("sub") or claims.get("id") or "")
    if not sub:
        raise ValueError("casdoor claims missing sub/id")

    username = (
        claims.get("preferred_username")
        or claims.get("name")
        or claims.get("username")
        or sub
    )
    email = claims.get("email")
    display_name = claims.get("displayName") or claims.get("name")
    avatar = claims.get("avatar") or claims.get("picture")

    result = await db.execute(select(User).where(User.casdoor_sub == sub))
    user = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)

    if user is None:
        user = User(
            casdoor_sub=sub,
            username=str(username),
            email=email,
            display_name=display_name,
            avatar_url=avatar,
            roles=roles,
            is_active=True,
            last_login_at=now,
            last_login_ip=ip,
        )
        db.add(user)
        await db.flush()
        await db.refresh(user)
        return user

    # Refresh cached fields on every login
    user.username = str(username)
    user.email = email
    user.display_name = display_name
    user.avatar_url = avatar
    # Only overwrite roles when Casdoor supplies a non-empty set. Empty means
    # "Casdoor has no role mapping for this user on this app" — do not clobber
    # roles that were granted locally (SQL bootstrap or /api/admin/users patch).
    if roles:
        user.roles = roles
    user.last_login_at = now
    user.last_login_ip = ip
    await db.flush()
    return user
