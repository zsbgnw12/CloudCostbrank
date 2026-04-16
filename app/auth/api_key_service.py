"""API key generation & verification."""

import hashlib
import secrets
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_key import ApiKey


_PREFIX = "cck_"   # cloudcost key


def generate_key() -> tuple[str, str, str]:
    """Returns (plaintext, key_hash, key_prefix)."""
    raw = secrets.token_urlsafe(36)
    plaintext = f"{_PREFIX}{raw}"
    key_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    key_prefix = plaintext[: len(_PREFIX) + 8]  # e.g. cck_ab12cdef
    return plaintext, key_hash, key_prefix


async def find_active_key(db: AsyncSession, plaintext: str) -> ApiKey | None:
    if not plaintext:
        return None
    key_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    result = await db.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
    ak = result.scalar_one_or_none()
    if ak is None:
        return None
    now = datetime.now(timezone.utc)
    if ak.revoked_at is not None:
        return None
    if ak.expires_at is not None and ak.expires_at < now:
        return None
    ak.last_used_at = now
    await db.flush()
    return ak
