"""
GCP Auth & Token Caching Service
Caches Google Cloud Identity / IAM tokens in Redis and in-memory to eliminate
redundant 5-second round-trips to the computeMetadata server on tool calls.
"""
import os
import time
import json
import logging
import httpx
from typing import Optional

logger = logging.getLogger(__name__)

_MEM_TOKEN_CACHE: dict = {}
_HTTPX_CLIENT: Optional[httpx.AsyncClient] = None


def get_shared_httpx_client() -> httpx.AsyncClient:
    """Returns a shared httpx.AsyncClient with connection pooling and keep-alive."""
    global _HTTPX_CLIENT
    if _HTTPX_CLIENT is None or _HTTPX_CLIENT.is_closed:
        _HTTPX_CLIENT = httpx.AsyncClient(
            timeout=25.0,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=50, keepalive_expiry=60.0)
        )
    return _HTTPX_CLIENT


async def get_cached_gcp_identity_token(audience: str) -> Optional[str]:
    """
    Retrieves a cached GCP Identity Token for the specified audience.
    Checks:
    1. In-memory dictionary (< 0.01 ms)
    2. Redis cache (< 1 ms)
    3. Metadata server / local credentials fallback (refreshes cache with 50m TTL)
    """
    now = time.time()
    cache_key = f"gcp_id_token:{audience}"

    # 1. Fast in-memory check (margin: 180 seconds before expiry)
    if cache_key in _MEM_TOKEN_CACHE:
        token, expiry = _MEM_TOKEN_CACHE[cache_key]
        if now < (expiry - 180):
            return token

    # 2. Redis check
    try:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        import redis.asyncio as aioredis
        r = aioredis.from_url(redis_url, socket_timeout=1.0)
        cached_val = await r.get(cache_key)
        await r.aclose()
        if cached_val:
            data = json.loads(cached_val)
            token = data.get("token")
            expiry = data.get("expiry", 0)
            if now < (expiry - 180):
                _MEM_TOKEN_CACHE[cache_key] = (token, expiry)
                return token
    except Exception:
        pass

    # 3. Fetch fresh token from GCP metadata server
    client = get_shared_httpx_client()
    fresh_token = None
    try:
        token_resp = await client.get(
            "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity",
            params={"audience": audience},
            headers={"Metadata-Flavor": "Google"},
            timeout=3.0
        )
        if token_resp.status_code == 200:
            fresh_token = token_resp.text.strip()
    except Exception as meta_err:
        logger.warning(f"[GCPAuthCache] Metadata server fetch failed: {meta_err}")

    # Fallback to local credentials file if on non-GCP or scope-restricted VM
    if not fresh_token:
        cred_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if not cred_path or not os.path.exists(cred_path):
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            candidates = [
                "/app/secrets/gcs-credentials.json",
                os.path.join(base_dir, "secrets", "gcs-credentials.json"),
                "/opt/stl-voicebot/secrets/gcs-credentials.json"
            ]
            for c in candidates:
                if os.path.exists(c):
                    cred_path = c
                    break

        if cred_path and os.path.exists(cred_path):
            try:
                from google.auth.transport.requests import Request
                from google.oauth2 import id_token
                auth_req = Request()
                fresh_token = id_token.fetch_id_token(auth_req, audience)
            except Exception as cred_err:
                logger.warning(f"[GCPAuthCache] id_token fetch fallback notice: {cred_err}")

    if fresh_token:
        expiry = now + 3000  # 50 minutes TTL
        _MEM_TOKEN_CACHE[cache_key] = (fresh_token, expiry)
        try:
            redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
            import redis.asyncio as aioredis
            r = aioredis.from_url(redis_url, socket_timeout=1.0)
            await r.setex(cache_key, 3000, json.dumps({"token": fresh_token, "expiry": expiry}))
            await r.aclose()
        except Exception:
            pass
        logger.info(f"[GCPAuthCache] Cached new token for audience {audience} (TTL: 3000s)")
        return fresh_token

    return None
