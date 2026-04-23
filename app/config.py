"""CloudCost application configuration."""

from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    # Database (Azure PostgreSQL) — set real values in .env, not here
    DATABASE_URL: str = "postgresql+asyncpg://user:password@localhost:5432/cloudcost"
    SYNC_DATABASE_URL: str = "postgresql+psycopg2://user:password@localhost:5432/cloudcost"
    # asyncpg TLS：None=按 DATABASE_URL 推断（localhost/127.0.0.1 为 false，否则 true）；也可显式 true/false
    DATABASE_SSL: Optional[bool] = None

    # Redis — set real values in .env, not here
    REDIS_URL: str = "redis://localhost:6379/0"

    # AES encryption key (Fernet key, base64-encoded 32 bytes)
    AES_SECRET_KEY: str = ""

    # SMTP for alert emails.
    # 两种典型配置：
    #   • Gmail / Outlook：SMTP_PORT=587, SMTP_USE_TLS=true, SMTP_USE_SSL=false  (STARTTLS)
    #   • 189.cn / QQ / 网易：SMTP_PORT=465, SMTP_USE_SSL=true, SMTP_USE_TLS=false (隐式 SSL)
    # Gmail 的 SMTP_PASSWORD 是「应用专用密码」，不是登录密码。
    # SMTP_FROM 通常与 SMTP_USER 相同。
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "cloudcost@example.com"
    SMTP_USE_TLS: bool = True   # STARTTLS（587）
    SMTP_USE_SSL: bool = False  # 隐式 SSL（465）；两者任选其一，SSL 优先于 TLS

    # Azure AD (for AI model batch deployment — SPA public client, no secret needed)
    AZURE_AD_CLIENT_ID: str = ""
    AZURE_AD_TENANT_ID: str = ""
    AZURE_AD_REDIRECT_URI: str = "http://localhost:3000/azure-deploy"

    # Azure Multi-tenant App (for cross-tenant Cost Management access via SP consent)
    # This is OUR app registered in OUR tenant; customers grant admin consent to drop
    # a service principal into their tenant, then assign Cost Management Reader on subs.
    AZURE_APP_TENANT_ID: str = ""        # our home tenant id
    AZURE_APP_CLIENT_ID: str = ""        # our multi-tenant app (Application) id
    AZURE_APP_CLIENT_SECRET: str = ""    # our client secret (stays server-side only)

    # Azure Consent redirect
    PUBLIC_BASE_URL: str = "http://localhost:8000"          # backend public origin (for redirect_uri)
    FRONTEND_URL: str = "http://localhost:3000"             # frontend origin (for post-callback redirect)

    # App
    APP_TITLE: str = "CloudCost"
    APP_VERSION: str = "0.1.0"

    # CORS：留空则 allow_origins=["*"] 且 allow_credentials=False（符合浏览器规范）。
    # 需要携带 Cookie 跨域时，设为逗号分隔白名单，例如：http://localhost:3000,https://app.example.com
    CORS_ORIGINS: str = ""

    # ===== Casdoor (统一身份) =====
    CASDOOR_ENDPOINT: str = "https://casdoor.ashyglacier-8207efd2.eastasia.azurecontainerapps.io"
    CASDOOR_CLIENT_ID: str = ""
    CASDOOR_CLIENT_SECRET: str = ""
    CASDOOR_ORG: str = ""
    CASDOOR_APP_NAME: str = "cloudcost"
    CASDOOR_REDIRECT_URI: str = "http://localhost:8000/api/auth/callback"
    CASDOOR_FRONTEND_HOME: str = "http://localhost:3000/"
    # Casdoor token payload 里"角色列表"的 claim 名。
    # 默认 "roles"；Keycloak / 自定义映射场景可改成 "role" / "user_roles" / "realm_roles" 等。
    # 兼容：即便设置为其他值，代码仍会 fallback 读 "roles" 和 "role"。
    CASDOOR_ROLES_CLAIM: str = "roles"

    # ===== 云管自己的 JWT (HS256) =====
    CC_JWT_SECRET: str = "change-me-to-a-long-random-string"
    CC_JWT_ALGORITHM: str = "HS256"
    CC_JWT_ACCESS_TTL: int = 900
    CC_JWT_REFRESH_TTL: int = 7 * 24 * 3600
    CC_JWT_ISSUER: str = "cloudcost"

    # Cookie
    CC_ACCESS_COOKIE: str = "cc_access_token"
    CC_REFRESH_COOKIE: str = "cc_refresh_token"
    CC_COOKIE_SECURE: bool = False
    CC_COOKIE_SAMESITE: str = "lax"

    # 鉴权强制开关(灰度)
    AUTH_ENFORCED: bool = True
    AUTH_ANONYMOUS_PREFIXES: str = "/api/health,/api/auth,/docs,/redoc,/openapi.json"

    model_config = {"env_file": str(_ENV_FILE), "extra": "ignore"}


settings = Settings()
