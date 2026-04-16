"""AuthMiddleware — parse incoming credentials and attach Principal.

Three-way recognition, in order:
  1. X-API-Key header           → api_key
  2. Authorization: Bearer <t>  → inspect issuer:
       - iss == cloudcost  → cc_jwt
       - iss == casdoor    → casdoor_jwt (JWKS verified)
  3. Cookie cc_access_token     → cc_jwt (browser flow)

Failures don't raise here — they leave `request.state.principal = None`.
The `get_current_user` dependency or the ModulePermission guard decides
whether the route actually requires auth (per AUTH_ENFORCED + anonymous
prefixes).
"""

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from sqlalchemy import select

from app.auth.api_key_service import find_active_key
from app.auth.casdoor_client import CasdoorError, extract_roles, verify_casdoor_token
from app.auth.jwt_service import JwtError, verify_cc_access
from app.auth.principal import AuthMethod, Principal
from app.auth.user_service import upsert_from_casdoor
from app.config import settings
from app.database import async_session_factory
from app.models.user import User

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        request.state.principal = None
        try:
            principal = await self._resolve(request)
            request.state.principal = principal
        except Exception:  # defensive: auth parsing never bricks the request
            logger.exception("AuthMiddleware parse failure")
            request.state.principal = None
        return await call_next(request)

    async def _resolve(self, request: Request) -> Principal | None:
        # 1) API key
        api_key = request.headers.get("X-API-Key") or request.headers.get("x-api-key")
        if api_key:
            return await self._from_api_key(api_key)

        # 2) Authorization header
        auth = request.headers.get("authorization") or request.headers.get("Authorization")
        if auth and auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1].strip()
            principal = await self._from_bearer(token)
            if principal:
                return principal

        # 3) Cookie access token (cloudcost-issued JWT)
        cookie_token = request.cookies.get(settings.CC_ACCESS_COOKIE)
        if cookie_token:
            return await self._from_cc_jwt(cookie_token)

        return None

    # ---------------- API key ----------------
    async def _from_api_key(self, plaintext: str) -> Principal | None:
        async with async_session_factory() as db:
            try:
                ak = await find_active_key(db, plaintext)
                if not ak:
                    await db.commit()
                    return None
                user = await db.get(User, ak.owner_user_id)
                if not user or not user.is_active:
                    await db.commit()
                    return None
                p = Principal(
                    user=user,
                    method=AuthMethod.API_KEY,
                    restricted_modules=ak.allowed_modules,
                    restricted_cloud_account_ids=ak.allowed_cloud_account_ids,
                    roles=list(user.roles or []),
                )
                await db.commit()
                return p
            except Exception:
                await db.rollback()
                raise

    # ---------------- Bearer routing ----------------
    async def _from_bearer(self, token: str) -> Principal | None:
        # Fast path: cc-issued JWT
        try:
            payload = verify_cc_access(token)
            return await self._principal_for_user_id(int(payload["uid"]), AuthMethod.CC_JWT, payload.get("roles") or [])
        except JwtError:
            pass

        # Fallback: Casdoor-issued JWT (external systems forwarding their user token)
        try:
            payload = verify_casdoor_token(token)
        except CasdoorError:
            return None

        roles = extract_roles(payload)
        async with async_session_factory() as db:
            try:
                user = await upsert_from_casdoor(db, claims=payload, roles=roles)
                await db.commit()
                return Principal(user=user, method=AuthMethod.CASDOOR_JWT, roles=roles)
            except Exception:
                await db.rollback()
                return None

    # ---------------- cc-JWT from cookie ----------------
    async def _from_cc_jwt(self, token: str) -> Principal | None:
        try:
            payload = verify_cc_access(token)
        except JwtError:
            return None
        return await self._principal_for_user_id(int(payload["uid"]), AuthMethod.CC_JWT, payload.get("roles") or [])

    async def _principal_for_user_id(self, user_id: int, method: AuthMethod, roles: list[str]) -> Principal | None:
        async with async_session_factory() as db:
            user = await db.get(User, user_id)
            if not user or not user.is_active:
                return None
            return Principal(user=user, method=method, roles=roles or list(user.roles or []))
