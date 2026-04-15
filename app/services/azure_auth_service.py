"""Azure AD authentication — direct ARM token approach.

Frontend uses MSAL.js to pop up Azure login and requests ARM scope directly.
The resulting token IS the ARM token — no OBO exchange needed on the backend.
Backend simply decodes the JWT to extract user info and expiry.
"""

import logging
import time

import jwt

from app.config import settings

logger = logging.getLogger(__name__)


def get_msal_config() -> dict:
    """Return MSAL configuration for frontend initialization."""
    return {
        "client_id": settings.AZURE_AD_CLIENT_ID,
        "authority": f"https://login.microsoftonline.com/{settings.AZURE_AD_TENANT_ID}",
        "redirect_uri": settings.AZURE_AD_REDIRECT_URI,
        "scopes": ["https://management.azure.com/user_impersonation"],
    }


def decode_arm_token(arm_token: str) -> dict:
    """Decode the ARM token (without signature verification) to extract claims.

    Signature verification is unnecessary here because every subsequent
    Azure SDK call with this token will fail if it's forged — Azure ARM
    is the authoritative validator.

    Returns {"name", "email", "tenant_id", "expires_on"}.
    """
    try:
        claims = jwt.decode(arm_token, options={"verify_signature": False})
        return {
            "name": claims.get("name", ""),
            "email": claims.get("preferred_username", claims.get("upn", "")),
            "tenant_id": claims.get("tid", ""),
            "expires_on": claims.get("exp", int(time.time()) + 3600),
        }
    except jwt.PyJWTError as e:
        raise ValueError(f"ARM token 解码失败: {e}") from e
