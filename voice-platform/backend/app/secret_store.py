"""Encrypt provider secrets before they are stored. Secrets are write-only: nothing in the API returns them.

CREDENTIAL_ENCRYPTION_KEY holds one or more Fernet keys separated by commas. The first key encrypts; every key can
decrypt, so a key is rotated by putting the new one first and re-saving the secrets. Generate a key with:

    python -c "from app.secret_store import generate_key; print(generate_key())"
"""
import os
from typing import List

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class SecretStoreError(RuntimeError):
    """The secret store is not configured, or a stored secret cannot be decrypted with the configured keys."""


def generate_key() -> str:
    return Fernet.generate_key().decode()


def _keys() -> List[bytes]:
    raw = os.getenv("CREDENTIAL_ENCRYPTION_KEY", "")
    return [k.strip().encode() for k in raw.split(",") if k.strip()]


def is_configured() -> bool:
    return bool(_keys())


def _fernet() -> MultiFernet:
    keys = _keys()
    if not keys:
        raise SecretStoreError("CREDENTIAL_ENCRYPTION_KEY is not set, so secrets cannot be stored. Generate a key and add it to the server environment.")
    try:
        return MultiFernet([Fernet(k) for k in keys])
    except (ValueError, TypeError) as exc:
        raise SecretStoreError("CREDENTIAL_ENCRYPTION_KEY is not a valid Fernet key") from exc


def encrypt(text: str) -> str:
    return _fernet().encrypt(text.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise SecretStoreError("A stored secret could not be decrypted with the configured key(s)") from exc


def last4(secret: str) -> str:
    """A short hint so an operator can tell keys apart without seeing them."""
    secret = secret.strip()
    return secret[-4:] if len(secret) >= 8 else ""
