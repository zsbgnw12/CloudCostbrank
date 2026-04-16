"""HS256 JWT signing / verification for cloudcost's own access & refresh tokens."""

import hashlib
import secrets
import time
import uuid
from typing import Any

import jwt

from app.config import settings


class JwtError(Exception):
    pass


# ---------- Access token (short-lived, stateless) ----------

def sign_access_token(user_id: int, roles: list[str], extra: dict | None = None) -> tuple[str, int]:
    now = int(time.time())
    payload: dict[str, Any] = {
        "iss": settings.CC_JWT_ISSUER,
        "sub": str(user_id),
        "uid": user_id,
        "roles": roles,
        "iat": now,
        "exp": now + settings.CC_JWT_ACCESS_TTL,
        "typ": "access",
    }
    if extra:
        payload.update(extra)
    token = jwt.encode(payload, settings.CC_JWT_SECRET, algorithm=settings.CC_JWT_ALGORITHM)
    return token, settings.CC_JWT_ACCESS_TTL


def verify_cc_access(token: str) -> dict:
    try:
        payload = jwt.decode(
            token,
            settings.CC_JWT_SECRET,
            algorithms=[settings.CC_JWT_ALGORITHM],
            issuer=settings.CC_JWT_ISSUER,
        )
    except jwt.PyJWTError as e:
        raise JwtError(f"invalid cc access token: {e}") from e
    if payload.get("typ") != "access":
        raise JwtError("wrong token type")
    return payload


# ---------- Refresh token (opaque random, stored hashed in DB) ----------

def new_refresh_token() -> tuple[str, str, str, int]:
    """Returns (plaintext, jti, token_hash, ttl_seconds)."""
    jti = uuid.uuid4().hex
    raw = secrets.token_urlsafe(48)
    plaintext = f"{jti}.{raw}"
    token_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    return plaintext, jti, token_hash, settings.CC_JWT_REFRESH_TTL


def parse_refresh_token(plaintext: str) -> tuple[str, str]:
    """Returns (jti, token_hash). Raises JwtError on format issues."""
    if "." not in plaintext:
        raise JwtError("malformed refresh token")
    jti, _ = plaintext.split(".", 1)
    return jti, hashlib.sha256(plaintext.encode()).hexdigest()
