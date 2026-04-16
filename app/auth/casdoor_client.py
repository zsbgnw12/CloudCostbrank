"""Casdoor OIDC client — authorize URL, code exchange, JWKS verification, userinfo."""

import time
import urllib.parse
from typing import Any

import httpx
import jwt
from jwt import PyJWKClient

from app.config import settings


class CasdoorError(Exception):
    pass


def _endpoint() -> str:
    return settings.CASDOOR_ENDPOINT.rstrip("/")


# ------- URLs -------

def authorize_url(state: str, scope: str = "openid profile email") -> str:
    qs = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": settings.CASDOOR_CLIENT_ID,
        "redirect_uri": settings.CASDOOR_REDIRECT_URI,
        "scope": scope,
        "state": state,
    })
    return f"{_endpoint()}/login/oauth/authorize?{qs}"


def logout_url(post_logout_redirect: str | None = None) -> str:
    if post_logout_redirect:
        qs = urllib.parse.urlencode({"post_logout_redirect_uri": post_logout_redirect})
        return f"{_endpoint()}/api/logout?{qs}"
    return f"{_endpoint()}/api/logout"


# ------- Code exchange & userinfo -------

async def exchange_code(code: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{_endpoint()}/api/login/oauth/access_token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": settings.CASDOOR_CLIENT_ID,
                "client_secret": settings.CASDOOR_CLIENT_SECRET,
                "redirect_uri": settings.CASDOOR_REDIRECT_URI,
            },
            headers={"Accept": "application/json"},
        )
    if resp.status_code != 200:
        raise CasdoorError(f"exchange_code http {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    if "access_token" not in data:
        raise CasdoorError(f"exchange_code missing access_token: {data}")
    return data


async def userinfo(access_token: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{_endpoint()}/api/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if resp.status_code != 200:
        raise CasdoorError(f"userinfo http {resp.status_code}: {resp.text[:300]}")
    return resp.json()


# ------- JWKS verification (for method A: external systems forwarding Casdoor tokens) -------

_jwks_client: PyJWKClient | None = None
_jwks_client_endpoint: str = ""


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client, _jwks_client_endpoint
    ep = _endpoint()
    if _jwks_client is None or _jwks_client_endpoint != ep:
        _jwks_client = PyJWKClient(f"{ep}/.well-known/jwks", cache_keys=True, lifespan=3600)
        _jwks_client_endpoint = ep
    return _jwks_client


def verify_casdoor_token(token: str) -> dict[str, Any]:
    """Verify a JWT issued by Casdoor. Returns the decoded payload.

    Raises CasdoorError on any verification failure (bad signature, expired, wrong iss).
    """
    try:
        unverified = jwt.get_unverified_header(token)
        alg = unverified.get("alg", "RS256")
        signing_key = _get_jwks_client().get_signing_key_from_jwt(token).key
        payload = jwt.decode(
            token,
            signing_key,
            algorithms=[alg],
            # Casdoor's `iss` is the endpoint URL; tolerate trailing slash differences.
            options={"verify_aud": False},
        )
    except jwt.PyJWTError as e:
        raise CasdoorError(f"invalid casdoor token: {e}") from e

    iss = (payload.get("iss") or "").rstrip("/")
    expected = _endpoint()
    if iss and iss != expected:
        raise CasdoorError(f"casdoor token iss mismatch: got {iss}, expected {expected}")
    if payload.get("exp") and payload["exp"] < int(time.time()):
        raise CasdoorError("casdoor token expired")
    return payload


def extract_roles(payload: dict[str, Any]) -> list[str]:
    """Casdoor exposes roles under a few possible claims; normalize to list[str]."""
    raw = payload.get("roles") or payload.get("role") or []
    if isinstance(raw, str):
        return [raw]
    out: list[str] = []
    for item in raw:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            name = item.get("name") or item.get("displayName")
            if name:
                out.append(str(name))
    return out
