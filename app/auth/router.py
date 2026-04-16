"""/api/auth/* — Casdoor SSO + local JWT lifecycle."""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import casdoor_client
from app.auth.dependencies import get_current_principal
from app.auth.jwt_service import new_refresh_token, parse_refresh_token, sign_access_token
from app.auth.principal import Principal
from app.auth.scope import visible_cloud_account_ids
from app.auth.user_service import upsert_from_casdoor
from app.config import settings
from app.database import get_db
from app.models.auth_refresh_session import AuthRefreshSession
from app.models.user import User
from app.schemas.auth import CurrentUser, LoginUrlResponse, TokenPair


router = APIRouter()


# In-process state store for OAuth CSRF protection. For multi-instance deployments
# swap with Redis — kept in-memory here to avoid a new dep for the reference impl.
_state_store: dict[str, float] = {}
_STATE_TTL = 600  # 10 min


def _put_state() -> str:
    now = datetime.now(timezone.utc).timestamp()
    # purge stale
    expired = [k for k, v in _state_store.items() if v + _STATE_TTL < now]
    for k in expired:
        _state_store.pop(k, None)
    state = secrets.token_urlsafe(24)
    _state_store[state] = now
    return state


def _check_state(state: str) -> bool:
    now = datetime.now(timezone.utc).timestamp()
    ts = _state_store.pop(state, None)
    return ts is not None and ts + _STATE_TTL >= now


def _set_cookies(response: Response, access_token: str, refresh_token: str, access_ttl: int, refresh_ttl: int) -> None:
    response.set_cookie(
        settings.CC_ACCESS_COOKIE,
        access_token,
        max_age=access_ttl,
        httponly=True,
        secure=settings.CC_COOKIE_SECURE,
        samesite=settings.CC_COOKIE_SAMESITE,
        path="/",
    )
    response.set_cookie(
        settings.CC_REFRESH_COOKIE,
        refresh_token,
        max_age=refresh_ttl,
        httponly=True,
        secure=settings.CC_COOKIE_SECURE,
        samesite=settings.CC_COOKIE_SAMESITE,
        path="/api/auth",
    )


def _clear_cookies(response: Response) -> None:
    response.delete_cookie(settings.CC_ACCESS_COOKIE, path="/")
    response.delete_cookie(settings.CC_REFRESH_COOKIE, path="/api/auth")


async def _issue_session(db: AsyncSession, user: User, ip: str | None, user_agent: str | None) -> tuple[TokenPair, str]:
    """Sign access token + persist refresh session. Returns (pair, refresh_plaintext)."""
    access_token, access_ttl = sign_access_token(user.id, list(user.roles or []))

    plaintext, jti, token_hash, refresh_ttl = new_refresh_token()
    db.add(AuthRefreshSession(
        jti=jti,
        user_id=user.id,
        token_hash=token_hash,
        user_agent=user_agent,
        ip=ip,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=refresh_ttl),
    ))
    await db.flush()

    return TokenPair(access_token=access_token, expires_in=access_ttl), plaintext


# ---------------- Login start ----------------

@router.get("/login", response_model=LoginUrlResponse)
async def login_start(redirect: bool = False):
    """Return the Casdoor authorize URL. If `redirect=true`, 302 directly."""
    if not settings.CASDOOR_CLIENT_ID:
        raise HTTPException(500, "Casdoor not configured (CASDOOR_CLIENT_ID missing)")
    state = _put_state()
    url = casdoor_client.authorize_url(state)
    if redirect:
        return RedirectResponse(url)
    return LoginUrlResponse(authorize_url=url, state=state)


# ---------------- OAuth callback ----------------

@router.get("/callback")
async def oauth_callback(
    code: str,
    state: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    if not _check_state(state):
        raise HTTPException(400, "Invalid or expired state")

    try:
        tok = await casdoor_client.exchange_code(code)
    except casdoor_client.CasdoorError as e:
        raise HTTPException(400, f"Casdoor exchange failed: {e}")

    access = tok.get("access_token")
    id_token = tok.get("id_token")
    claims: dict = {}
    roles: list[str] = []

    if id_token:
        try:
            claims = casdoor_client.verify_casdoor_token(id_token)
            roles = casdoor_client.extract_roles(claims)
        except casdoor_client.CasdoorError:
            claims = {}

    # Always supplement with userinfo for avatar / displayName and to have roles.
    try:
        info = await casdoor_client.userinfo(access)
    except casdoor_client.CasdoorError as e:
        raise HTTPException(400, f"Casdoor userinfo failed: {e}")
    claims = {**info, **claims}  # id_token claims win
    if not roles:
        roles = casdoor_client.extract_roles(info)

    ip = request.client.host if request.client else None
    user = await upsert_from_casdoor(db, claims=claims, roles=roles, ip=ip)

    ua = request.headers.get("user-agent")
    pair, refresh_plain = await _issue_session(db, user, ip, ua)
    await db.commit()

    resp = RedirectResponse(settings.CASDOOR_FRONTEND_HOME)
    _set_cookies(resp, pair.access_token, refresh_plain, pair.expires_in, settings.CC_JWT_REFRESH_TTL)
    return resp


# ---------------- Refresh ----------------

@router.post("/refresh", response_model=TokenPair)
async def refresh_token(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    cc_refresh_token: str | None = Cookie(default=None, alias=settings.CC_REFRESH_COOKIE),
):
    refresh = cc_refresh_token
    if not refresh:
        # Allow body-based refresh for non-browser clients.
        try:
            body = await request.json()
            refresh = body.get("refresh_token") if isinstance(body, dict) else None
        except Exception:
            refresh = None
    if not refresh:
        raise HTTPException(401, "Missing refresh token")

    try:
        jti, token_hash = parse_refresh_token(refresh)
    except Exception:
        raise HTTPException(401, "Malformed refresh token")

    row = await db.execute(select(AuthRefreshSession).where(AuthRefreshSession.jti == jti))
    session = row.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if session is None or session.revoked_at is not None or session.token_hash != token_hash or session.expires_at < now:
        raise HTTPException(401, "Refresh token invalid or expired")

    user = await db.get(User, session.user_id)
    if not user or not user.is_active:
        raise HTTPException(401, "User disabled")

    # Rotate: revoke the old refresh session, issue a new one.
    session.revoked_at = now
    ua = request.headers.get("user-agent")
    ip = request.client.host if request.client else None
    pair, refresh_plain = await _issue_session(db, user, ip, ua)
    await db.commit()

    _set_cookies(response, pair.access_token, refresh_plain, pair.expires_in, settings.CC_JWT_REFRESH_TTL)
    return pair


# ---------------- Logout ----------------

@router.post("/logout")
async def logout(
    response: Response,
    db: AsyncSession = Depends(get_db),
    cc_refresh_token: str | None = Cookie(default=None, alias=settings.CC_REFRESH_COOKIE),
):
    if cc_refresh_token:
        try:
            jti, _ = parse_refresh_token(cc_refresh_token)
            row = await db.execute(select(AuthRefreshSession).where(AuthRefreshSession.jti == jti))
            session = row.scalar_one_or_none()
            if session and session.revoked_at is None:
                session.revoked_at = datetime.now(timezone.utc)
                await db.commit()
        except Exception:
            pass
    _clear_cookies(response)
    return {"ok": True}


# ---------------- Who am I ----------------

@router.get("/me", response_model=CurrentUser)
async def me(
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    user = principal.user
    visible = await visible_cloud_account_ids(db, principal)
    return CurrentUser(
        id=user.id,
        username=user.username,
        email=user.email,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        roles=list(user.roles or []),
        visible_cloud_account_ids=visible,
    )
