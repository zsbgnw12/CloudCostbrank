"""Auth / User / ApiKey / ModulePermission schemas."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ---------- User ----------

class UserRead(BaseModel):
    id: int
    casdoor_sub: str
    username: str
    email: Optional[str] = None
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    roles: list[str] = []
    is_active: bool
    last_login_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class CurrentUser(BaseModel):
    """Payload returned by GET /api/auth/me — adds effective data-scope."""

    id: int
    username: str
    email: Optional[str] = None
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    roles: list[str] = []
    # None means "full access" (cloud_admin); otherwise explicit list.
    visible_cloud_account_ids: Optional[list[int]] = None


# ---------- Login / token ----------

class LoginUrlResponse(BaseModel):
    authorize_url: str
    state: str


class TokenPair(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    refresh_token: Optional[str] = None  # omitted when delivered via HttpOnly cookie


# ---------- Grants ----------

class GrantCreate(BaseModel):
    user_id: int
    cloud_account_id: int
    scope: str = Field(default="READ", pattern="^(READ|WRITE)$")


class GrantRead(BaseModel):
    id: int
    user_id: int
    cloud_account_id: int
    scope: str
    granted_by: Optional[int] = None
    granted_at: datetime

    model_config = {"from_attributes": True}


# ---------- Module permission ----------

class ModulePermissionRead(BaseModel):
    module: str
    enabled: bool
    description: Optional[str] = None
    updated_at: datetime

    model_config = {"from_attributes": True}


class ModulePermissionToggle(BaseModel):
    enabled: bool


# ---------- API key ----------

class ApiKeyCreate(BaseModel):
    name: str
    owner_user_id: Optional[int] = None  # null = self
    allowed_modules: Optional[list[str]] = None
    allowed_cloud_account_ids: Optional[list[int]] = None
    expires_at: Optional[datetime] = None


class ApiKeyRead(BaseModel):
    id: int
    name: str
    key_prefix: str
    owner_user_id: int
    allowed_modules: Optional[list[str]] = None
    allowed_cloud_account_ids: Optional[list[int]] = None
    expires_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ApiKeyCreated(ApiKeyRead):
    """Shown exactly once, right after creation."""
    plaintext_key: str
