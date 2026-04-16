"""Azure multi-tenant SP consent onboarding — invite-based flow.

Flow (new):
1. POST /api/azure-consent/start       → operator creates invite, gets consent URL with state
2. GET  /api/azure-consent/callback     → Microsoft redirects customer here; auto-creates account
3. POST /api/azure-consent/verify/{id}  → operator verifies subscription discovery
4. GET  /api/azure-consent/subscriptions/{id} → live subscription list

Legacy (kept for rollback):
5. POST /api/azure-consent/register     → manually register tenant_id

Invite management:
6. GET  /api/azure-consent/invites          → list all invites
7. POST /api/azure-consent/invites/{id}/revoke  → cancel unused invite
"""

import secrets
from datetime import datetime, timedelta
from urllib.parse import urlencode

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.azure_consent_invite import AzureConsentInvite
from app.models.cloud_account import CloudAccount
from app.schemas.cloud_account import CloudAccountRead
from app.services.audit_service import log_operation
from app.services.crypto_service import decrypt_to_dict, encrypt_dict

router = APIRouter()

# Callback is a separate router so it can be mounted WITHOUT auth dependencies.
callback_router = APIRouter()


# ─── request / response models ──────────────────────────────────────────

class ConsentStartRequest(BaseModel):
    account_name: str = Field(..., description="Display name for this customer")


class ConsentStartResponse(BaseModel):
    consent_url: str
    expires_at: str
    instructions: str


class ConsentRegisterRequest(BaseModel):
    name: str = Field(..., description="Display name for this cloud account")
    tenant_id: str = Field(..., description="Customer's Azure AD tenant id (GUID)")
    subscription_ids: list[str] = Field(default_factory=list)


class VerifyResponse(BaseModel):
    ok: bool
    message: str
    discovered_subscriptions: list[dict] = Field(default_factory=list)


class InviteRead(BaseModel):
    id: int
    state: str
    account_name: str
    status: str
    cloud_account_id: int | None = None
    created_by: int | None = None
    created_at: str
    expires_at: str
    consumed_at: str | None = None
    error_reason: str | None = None


# ─── helpers ────────────────────────────────────────────────────────────

def _require_global_app():
    if not settings.AZURE_APP_CLIENT_ID or not settings.AZURE_APP_CLIENT_SECRET:
        raise HTTPException(
            500,
            "Backend AZURE_APP_CLIENT_ID / AZURE_APP_CLIENT_SECRET not configured."
        )


def _acquire_arm_token(tenant_id: str) -> str:
    """client_credentials against the customer tenant → ARM token for our SP."""
    _require_global_app()
    url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    resp = requests.post(
        url,
        data={
            "grant_type": "client_credentials",
            "client_id": settings.AZURE_APP_CLIENT_ID,
            "client_secret": settings.AZURE_APP_CLIENT_SECRET,
            "scope": "https://management.azure.com/.default",
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise HTTPException(
            400,
            f"Failed to acquire token from tenant {tenant_id}: {resp.status_code} {resp.text}"
        )
    return resp.json()["access_token"]


def _list_subscriptions(arm_token: str) -> list[dict]:
    """ARM: list subscriptions our SP can see in the customer tenant."""
    resp = requests.get(
        "https://management.azure.com/subscriptions?api-version=2020-01-01",
        headers={"Authorization": f"Bearer {arm_token}"},
        timeout=30,
    )
    if resp.status_code != 200:
        return []
    out = []
    for sub in resp.json().get("value", []):
        out.append({
            "subscription_id": sub.get("subscriptionId"),
            "display_name": sub.get("displayName"),
            "state": sub.get("state"),
        })
    return out


def _invite_to_dict(inv: AzureConsentInvite) -> dict:
    return {
        "id": inv.id,
        "state": inv.state,
        "account_name": inv.account_name,
        "status": inv.status,
        "cloud_account_id": inv.cloud_account_id,
        "created_by": inv.created_by,
        "created_at": inv.created_at.isoformat() if inv.created_at else None,
        "expires_at": inv.expires_at.isoformat() if inv.expires_at else None,
        "consumed_at": inv.consumed_at.isoformat() if inv.consumed_at else None,
        "error_reason": inv.error_reason,
    }


# ─── endpoints (authenticated, on main router) ─────────────────────────

@router.post("/start", response_model=ConsentStartResponse)
async def consent_start(body: ConsentStartRequest, db: AsyncSession = Depends(get_db)):
    """Generate an invite with a unique state and return the admin-consent URL."""
    _require_global_app()

    state = secrets.token_urlsafe(32)
    invite = AzureConsentInvite(
        state=state,
        account_name=body.account_name,
        status="pending",
        expires_at=datetime.utcnow() + timedelta(hours=24),
    )
    db.add(invite)
    await db.flush()

    redirect_uri = f"{settings.PUBLIC_BASE_URL}/api/azure-consent/callback"
    qs = urlencode({
        "client_id": settings.AZURE_APP_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "state": state,
    })
    url = f"https://login.microsoftonline.com/organizations/adminconsent?{qs}"

    await log_operation(
        db, action="create_consent_invite", target_type="azure_consent_invite",
        target_id=invite.id, after_data={"account_name": body.account_name, "state": state},
    )

    return ConsentStartResponse(
        consent_url=url,
        expires_at=invite.expires_at.isoformat(),
        instructions=(
            "1) 将此链接发给客户全局管理员，客户在任意电脑上点击即可。\n"
            "2) 客户登录自己 Azure AD → 同意，浏览器会自动跳回我们平台。\n"
            "3) 客户继续在 Azure 门户给目标订阅分配 Cost Management Reader 角色。"
        ),
    )


@router.get("/invites")
async def list_invites(db: AsyncSession = Depends(get_db)):
    """List all consent invites (operator view)."""
    result = await db.execute(
        select(AzureConsentInvite).order_by(AzureConsentInvite.created_at.desc())
    )
    invites = result.scalars().all()
    return [_invite_to_dict(inv) for inv in invites]


@router.post("/invites/{invite_id}/revoke")
async def revoke_invite(invite_id: int, db: AsyncSession = Depends(get_db)):
    """Revoke an unused invite."""
    invite = await db.get(AzureConsentInvite, invite_id)
    if not invite:
        raise HTTPException(404, "Invite not found")
    if invite.status != "pending":
        raise HTTPException(400, f"Cannot revoke invite in status '{invite.status}'")
    invite.status = "expired"
    await log_operation(
        db, action="revoke_consent_invite", target_type="azure_consent_invite",
        target_id=invite.id, after_data={"status": "expired"},
    )
    return {"ok": True, "message": "邀请已作废"}


# ─── legacy register (kept for rollback) ───────────────────────────────

@router.post("/register", response_model=CloudAccountRead, status_code=201)
async def consent_register(body: ConsentRegisterRequest, db: AsyncSession = Depends(get_db)):
    """Create a cloud_account in multi_tenant mode after customer consent (legacy manual flow)."""
    _require_global_app()
    secret = {
        "auth_mode": "multi_tenant",
        "tenant_id": body.tenant_id,
        "subscription_ids": body.subscription_ids,
    }
    account = CloudAccount(
        name=body.name,
        provider="azure",
        secret_data=encrypt_dict(secret),
        auth_mode="multi_tenant",
        consent_status="pending",
    )
    db.add(account)
    await db.flush()
    await db.refresh(account)
    await log_operation(
        db, action="register_azure_multi_tenant", target_type="cloud_account",
        target_id=account.id, after_data={"name": body.name, "tenant_id": body.tenant_id},
    )
    return account


# ─── verify & subscriptions ────────────────────────────────────────────

@router.post("/verify/{account_id}", response_model=VerifyResponse)
async def consent_verify(account_id: int, db: AsyncSession = Depends(get_db)):
    """Probe the customer tenant with our SP; on success flip consent_status=granted
    and auto-fill discovered subscriptions if none saved yet."""
    account = await db.get(CloudAccount, account_id)
    if not account or account.provider != "azure":
        raise HTTPException(404, "Azure cloud account not found")
    if account.auth_mode != "multi_tenant":
        raise HTTPException(400, "Account is not in multi_tenant mode")

    secret = decrypt_to_dict(account.secret_data)
    tenant_id = secret.get("tenant_id")
    if not tenant_id:
        raise HTTPException(400, "tenant_id missing from account secret")

    try:
        token = _acquire_arm_token(tenant_id)
    except HTTPException as e:
        account.consent_status = "pending"
        await db.commit()
        return VerifyResponse(ok=False, message=f"Token acquisition failed: {e.detail}")

    subs = _list_subscriptions(token)
    if not subs:
        account.consent_status = "granted"  # consent worked but no RBAC yet
        await db.commit()
        return VerifyResponse(
            ok=True,
            message="Consent OK，但尚未检测到任何订阅。请在目标订阅上分配 Cost Management Reader 角色。",
            discovered_subscriptions=[],
        )

    if not secret.get("subscription_ids"):
        secret["subscription_ids"] = [s["subscription_id"] for s in subs]
        account.secret_data = encrypt_dict(secret)
    account.consent_status = "granted"
    await db.commit()
    return VerifyResponse(
        ok=True,
        message=f"验证成功，检测到 {len(subs)} 个订阅。",
        discovered_subscriptions=subs,
    )


@router.get("/subscriptions/{account_id}")
async def list_account_subscriptions(account_id: int, db: AsyncSession = Depends(get_db)):
    """Live list of subscriptions our SP can currently see in the customer tenant."""
    account = await db.get(CloudAccount, account_id)
    if not account or account.provider != "azure":
        raise HTTPException(404, "Azure cloud account not found")
    if account.auth_mode != "multi_tenant":
        raise HTTPException(400, "Account is not in multi_tenant mode")

    secret = decrypt_to_dict(account.secret_data)
    token = _acquire_arm_token(secret["tenant_id"])
    return {"subscriptions": _list_subscriptions(token)}


# ─── callback (public, NO auth — on separate router) ──────────────────

@callback_router.get("/callback")
async def consent_callback(
    tenant: str | None = Query(None),
    state: str | None = Query(None),
    admin_consent: str | None = Query(None),
    error: str | None = Query(None),
    error_description: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Microsoft redirects customer browser here after admin consent.
    This endpoint is public (no auth) — mounted on callback_router."""
    frontend = settings.FRONTEND_URL.rstrip("/")

    # 1. Validate state
    if not state:
        return RedirectResponse(f"{frontend}/consent-fail?reason=invalid_state")

    invite = await db.scalar(
        select(AzureConsentInvite).where(AzureConsentInvite.state == state)
    )
    if not invite:
        return RedirectResponse(f"{frontend}/consent-fail?reason=invalid_state")
    if invite.status != "pending":
        return RedirectResponse(f"{frontend}/consent-fail?reason=already_used")
    if invite.expires_at < datetime.utcnow():
        invite.status = "expired"
        await db.commit()
        return RedirectResponse(f"{frontend}/consent-fail?reason=expired")

    # 2. Customer denied or error
    if error or admin_consent != "True" or not tenant:
        invite.status = "failed"
        invite.error_reason = error_description or error or "unknown"
        await db.commit()
        reason = error or "denied"
        return RedirectResponse(f"{frontend}/consent-fail?reason={reason}")

    # 3. Idempotent: check if this tenant already has an account
    existing_accounts = (await db.execute(
        select(CloudAccount).where(
            CloudAccount.provider == "azure",
            CloudAccount.auth_mode == "multi_tenant",
        )
    )).scalars().all()

    for acc in existing_accounts:
        try:
            sec = decrypt_to_dict(acc.secret_data)
            if sec.get("tenant_id") == tenant:
                # Reuse existing account
                invite.status = "consumed"
                invite.cloud_account_id = acc.id
                invite.consumed_at = datetime.utcnow()
                await db.commit()
                return RedirectResponse(f"{frontend}/consent-success?account_id={acc.id}")
        except Exception:
            continue

    # 4. Create new cloud_account
    secret = {
        "auth_mode": "multi_tenant",
        "tenant_id": tenant,
        "subscription_ids": [],
    }
    account = CloudAccount(
        name=invite.account_name,
        provider="azure",
        secret_data=encrypt_dict(secret),
        auth_mode="multi_tenant",
        consent_status="granted",
    )
    db.add(account)
    await db.flush()

    invite.status = "consumed"
    invite.cloud_account_id = account.id
    invite.consumed_at = datetime.utcnow()

    await log_operation(
        db, action="consent_callback_auto_create", target_type="cloud_account",
        target_id=account.id,
        after_data={"name": invite.account_name, "tenant_id": tenant, "invite_id": invite.id},
    )
    await db.commit()

    return RedirectResponse(f"{frontend}/consent-success?account_id={account.id}")
