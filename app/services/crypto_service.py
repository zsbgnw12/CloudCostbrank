"""AES encryption/decryption for cloud account credentials using Fernet (AES-128-CBC + HMAC)."""

import json

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


def _get_fernet() -> Fernet:
    key = settings.AES_SECRET_KEY
    if not key:
        raise RuntimeError("AES_SECRET_KEY is not configured")
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_dict(data: dict) -> str:
    """Encrypt a dict to a Fernet token string."""
    plaintext = json.dumps(data, ensure_ascii=False).encode("utf-8")
    return _get_fernet().encrypt(plaintext).decode("ascii")


def decrypt_to_dict(token: str) -> dict:
    """Decrypt a Fernet token string back to a dict."""
    try:
        plaintext = _get_fernet().decrypt(token.encode("ascii"))
        return json.loads(plaintext)
    except InvalidToken:
        raise ValueError("Failed to decrypt: invalid token or wrong key")
