"""Unified principal wrapper — whichever auth path a request arrives on
(cloudcost-issued JWT, Casdoor-issued JWT forwarded by another system, or
an API key), it ends up as a `Principal` attached to `request.state.principal`.
"""

from dataclasses import dataclass, field
from enum import Enum

from app.models.user import User


class AuthMethod(str, Enum):
    CC_JWT = "cc_jwt"
    CASDOOR_JWT = "casdoor_jwt"
    API_KEY = "api_key"


@dataclass
class Principal:
    user: User
    method: AuthMethod
    # For API-key requests these override the user's effective scope:
    #   - None means "inherit from user"
    #   - a list (possibly empty) restricts further
    restricted_modules: list[str] | None = None
    restricted_cloud_account_ids: list[int] | None = None
    # Raw roles as observed on this request (api-key may carry nothing here)
    roles: list[str] = field(default_factory=list)

    @property
    def user_id(self) -> int:
        return self.user.id

    @property
    def is_admin(self) -> bool:
        # Casdoor 是单一权威源:只信 principal.roles。这个字段已由 middleware 按
        # 认证方式正确填充(Casdoor JWT 用 token roles,机器应用 fallback DB,
        # API key 用 owner DB roles)。再读 user.roles 做并集会让 Casdoor 后台
        # 撤销角色失效(DB 里的旧角色复活)。
        return "cloud_admin" in (self.roles or [])
