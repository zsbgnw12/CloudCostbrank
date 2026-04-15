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

    # SMTP for alert emails (Gmail: use smtp.gmail.com:587, SMTP_USER=full address,
    # SMTP_PASSWORD=Google「应用专用密码」not login password; SMTP_FROM 通常与 SMTP_USER 相同)
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "cloudcost@example.com"
    SMTP_USE_TLS: bool = True

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

    # App
    APP_TITLE: str = "CloudCost"
    APP_VERSION: str = "0.1.0"

    # CORS：留空则 allow_origins=["*"] 且 allow_credentials=False（符合浏览器规范）。
    # 需要携带 Cookie 跨域时，设为逗号分隔白名单，例如：http://localhost:3000,https://app.example.com
    CORS_ORIGINS: str = ""

    model_config = {"env_file": str(_ENV_FILE), "extra": "ignore"}


settings = Settings()
