"""Live health of the configured LLM providers, cached so the builder and call setup stay fast.

A key can be present and still unusable (billing cap, no access to the model, retired model).
One tiny request per provider, at most every TTL seconds, tells us.
"""
import asyncio
import hashlib
import logging
import time
from datetime import datetime
from typing import Any, Dict, Optional

import httpx

from .config import AppConfig

logger = logging.getLogger(__name__)

TTL_SECONDS = 600
PROBE_TIMEOUT = 6.0
_PROMPT = "Reply with the single word: pong"

_cache: Dict[str, Dict[str, Any]] = {}
_refreshing: Optional[asyncio.Task] = None


def _issue(status: int, body: str) -> str:
    text = body.lower()
    if status == 429 or "spending cap" in text or "quota" in text or "billing" in text:
        return "quota or billing limit reached"
    if status == 403:
        return "this key has no access to the model"
    if status == 404:
        return "the model is not available"
    if status in (401, 400) and ("api key" in text or "api_key" in text or status == 401):
        return "the API key was rejected"
    return f"HTTP {status}"


def _key_for(cfg: AppConfig, provider: str) -> str:
    return {"groq": cfg.llm.groq_api_key, "gemini": cfg.llm.gemini_api_key}.get(provider, cfg.llm.openai_api_key) or ""


def _fingerprint(key: str) -> str:
    """Which key a probe result belongs to (a one-way hash; the key itself is never kept)."""
    return hashlib.sha256((key or "").encode()).hexdigest()[:16]


async def _probe(client: httpx.AsyncClient, provider: str, cfg: AppConfig) -> Dict[str, Any]:
    from .provider_catalog import default_model

    model = default_model(cfg, provider)
    try:
        if provider == "gemini":
            r = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"x-goog-api-key": cfg.llm.gemini_api_key},
                json={"contents": [{"parts": [{"text": _PROMPT}]}], "generationConfig": {"maxOutputTokens": 16}})
        else:
            base, key = ((cfg.llm.groq_base_url, cfg.llm.groq_api_key) if provider == "groq"
                         else (cfg.llm.openai_base_url, cfg.llm.openai_api_key))
            r = await client.post(
                base.rstrip("/") + "/chat/completions", headers={"Authorization": f"Bearer {key}"},
                json={"model": model, "messages": [{"role": "user", "content": _PROMPT}], "max_tokens": 64})
        ok = r.status_code == 200
        return {"ok": ok, "issue": None if ok else _issue(r.status_code, r.text), "model": model}
    except httpx.TimeoutException:
        return {"ok": False, "issue": "the provider did not respond in time", "model": model}
    except Exception as exc:
        return {"ok": False, "issue": f"could not connect ({type(exc).__name__})", "model": model}


async def llm_health(cfg: AppConfig, force: bool = False) -> Dict[str, Dict[str, Any]]:
    """Health of each LLM provider that has a key. Reuses results younger than TTL_SECONDS."""
    from .provider_catalog import provider_available

    now = time.time()
    due = [p for p in ("groq", "openai", "gemini")
           if provider_available(cfg, "llm", p) and (force or now - _cache.get(p, {}).get("t", 0) > TTL_SECONDS)]
    if due:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
            results = await asyncio.gather(*[_probe(client, p, cfg) for p in due])
        for p, res in zip(due, results):
            _cache[p] = {**res, "t": now, "fp": _fingerprint(_key_for(cfg, p)), "checked_at": datetime.utcnow().isoformat()}
            if not res["ok"]:
                logger.warning(f"[LLMHealth] {p} is not working: {res['issue']}")
    return {p: {k: x for k, x in v.items() if k != "fp"} for p, v in _cache.items()}


def cached_llm_health() -> Dict[str, Dict[str, Any]]:
    """Whatever we last learned, without any network call (empty until the first probe)."""
    return {p: {k: x for k, x in v.items() if k != "fp"} for p, v in _cache.items()}


def known_unhealthy(provider: str, cfg: Optional[AppConfig] = None) -> Optional[str]:
    """The issue text if this provider last failed its probe, else None.

    Pass the configuration a call will actually use: a failure seen with the platform's key says nothing
    about an organization's own key, so it is ignored when the keys differ."""
    provider = (provider or "").lower()
    entry = _cache.get(provider)
    if not entry or entry.get("ok"):
        return None
    if cfg is not None and entry.get("fp") and entry["fp"] != _fingerprint(_key_for(cfg, provider)):
        return None
    return entry.get("issue")


def same_key(a: AppConfig, b: AppConfig, provider: str) -> bool:
    """True when both configurations hold the same key for this provider (so one probe result covers both)."""
    return _key_for(a, provider) == _key_for(b, provider)


async def probe_key(cfg: AppConfig, provider: str) -> Dict[str, Any]:
    """One live request with exactly the key in `cfg` (used to check an organization's key). Not cached."""
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
        return await _probe(client, provider, cfg)


def refresh_in_background(cfg: AppConfig) -> None:
    """Kick off a probe if results are stale; never blocks and never raises."""
    global _refreshing
    if _refreshing is not None and not _refreshing.done():
        return
    if _cache and all(time.time() - v.get("t", 0) <= TTL_SECONDS for v in _cache.values()):
        return
    try:
        _refreshing = asyncio.get_running_loop().create_task(llm_health(cfg))
    except RuntimeError:
        pass


def reset_cache() -> None:
    _cache.clear()
