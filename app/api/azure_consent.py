"""Azure multi-tenant SP consent onboarding.

Flow:
1. POST /api/azure-consent/start  → returns an admin-consent URL for the customer admin
2. POST /api/azure-consent/register → operator saves tenant_id + subscriptions after consent
3. POST /api/azure-consent/verify/{account_id} → probes Cost Management with our SP
4. GET  /api/azure-consent/subscriptions/{account_id} → auto-discover subs the SP can see
"""

from urllib.parse import urlencode

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.cloud_account import CloudAccount
from app.schemas.cloud_account import CloudAccountRead
from app.services.audit_service import log_operation
from app.services.crypto_service import decrypt_to_dict, encrypt_dict

router = APIRouter()


# ─── request / response models ──────────────────────────────────────────

class ConsentStartResponse(BaseModel):
    consent_url: str
    app_client_id: str
    instructions: str


class ConsentRegisterRequest(BaseModel):
    name: str = Field(..., description="Display name for this cloud account")
    tenant_id: str = Field(..., description="Customer's Azure AD tenant id (GUID)")
    subscription_ids: list[str] = Field(default_factory=list)


class VerifyResponse(BaseModel):
    ok: bool
    message: str
    discovered_subscriptions: list[dict] = Field(default_factory=list)


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


# ─── endpoints ──────────────────────────────────────────────────────────

@router.get("/start", response_model=ConsentStartResponse)
async def consent_start():
    """Build the admin-consent URL. Operator sends this link to the customer admin."""
    _require_global_app()
    qs = urlencode({"client_id": settings.AZURE_APP_CLIENT_ID})
    url = f"https://login.microsoftonline.com/organizations/adminconsent?{qs}"
    return ConsentStartResponse(
        consent_url=url,
        app_client_id=settings.AZURE_APP_CLIENT_ID,
        instructions=(
            "1) 客户全局管理员点击此链接并同意；\n"
            "2) 客户在每个需要监控的订阅 → 访问控制(IAM) → 添加角色分配 → "
            "角色选 'Cost Management Reader'，成员搜索我方应用名并选中；\n"
            "3) 回到本系统填入客户 Tenant ID（可在 Azure 门户 → Microsoft Entra ID 首页看到）。"
        ),
    )


@router.post("/register", response_model=CloudAccountRead, status_code=201)
async def consent_register(body: ConsentRegisterRequest, db: AsyncSession = Depends(get_db)):
    """Create a cloud_account in multi_tenant mode after customer consent."""
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
    await db.commit()
    return account


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
