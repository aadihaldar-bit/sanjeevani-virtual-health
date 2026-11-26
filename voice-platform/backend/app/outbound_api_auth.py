"""
API key generation, hashing, verification, and rate-limiting for the Outbound Voice API.
"""
import hashlib
import secrets
import logging
from datetime import datetime, timedelta
from typing import Tuple, Optional

from fastapi import Request, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db, async_session
from .models import APIClient

logger = logging.getLogger(__name__)

KEY_PREFIX = "stl_outbound_"

def generate_api_key() -> Tuple[str, str, str]:
    """
    Generates a secure API key.
    Returns: (plaintext_key, key_hash, key_prefix)
    """
    raw = secrets.token_hex(20)
    plaintext_key = f"{KEY_PREFIX}{raw}"
    key_hash = hashlib.sha256(plaintext_key.encode("utf-8")).hexdigest()
    prefix = plaintext_key[:12]
    return plaintext_key, key_hash, prefix

def hash_api_key(plaintext_key: str) -> str:
    """Hashes a plaintext API key with SHA-256."""
    return hashlib.sha256(plaintext_key.strip().encode("utf-8")).hexdigest()

async def verify_api_key(request: Request, db: AsyncSession = Depends(get_db)) -> APIClient:
    """
    FastAPI dependency that validates the Bearer API key in the Authorization header.
    Returns the authenticated APIClient database object.
    """
    auth_header = request.headers.get("authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header. Expected: Bearer stl_outbound_...")

    token = auth_header.split(" ", 1)[1].strip()
    if not token.startswith(KEY_PREFIX):
        raise HTTPException(status_code=401, detail=f"Invalid API key format. Key must start with '{KEY_PREFIX}'")

    token_hash = hash_api_key(token)
    stmt = select(APIClient).where(APIClient.api_key_hash == token_hash, APIClient.is_active == True)
    result = await db.execute(stmt)
    client = result.scalars().first()

    if not client:
        logger.warning(f"[OutboundAPIAuth] Rejected invalid API key: {token[:12]}...")
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")

    # Check and reset daily/monthly quota counters if needed
    now = datetime.utcnow()
    if not client.last_quota_reset or client.last_quota_reset.date() < now.date():
        client.calls_today = 0
        client.last_quota_reset = now
        if client.last_quota_reset.month != now.month:
            client.calls_this_month = 0
        await db.commit()

    # Check daily quota
    if client.calls_today >= client.daily_call_quota:
        raise HTTPException(status_code=429, detail=f"Daily call quota ({client.daily_call_quota}) exceeded for API client '{client.client_name}'")

    # Check monthly quota
    if client.calls_this_month >= client.monthly_call_quota:
        raise HTTPException(status_code=429, detail=f"Monthly call quota ({client.monthly_call_quota}) exceeded for API client '{client.client_name}'")

    return client
