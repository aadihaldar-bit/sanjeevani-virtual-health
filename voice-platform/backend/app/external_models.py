"""Bring-your-own models: an organization's own provider keys and its own OpenAI-compatible endpoints.

Keys are encrypted at rest (secret_store), accepted but never returned, and never written to logs or the audit trail.
At call time an organization's key replaces the platform key for that call only (apply_org_credentials), and a workflow
may choose a custom endpoint as its language model (provider id "custom:<endpoint id>").
"""
import dataclasses
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import secret_store
from .config import AppConfig
from .models import ModelEndpoint, ProviderCredential
from .net_guard import UnsafeURL, avalidate_external_url

logger = logging.getLogger(__name__)

# (kind, provider) -> (section of AppConfig, field that holds the key)
CREDENTIAL_FIELDS: Dict[Tuple[str, str], Tuple[str, str]] = {
    ("llm", "groq"): ("llm", "groq_api_key"),
    ("llm", "openai"): ("llm", "openai_api_key"),
    ("llm", "gemini"): ("llm", "gemini_api_key"),
    ("stt", "cartesia"): ("stt", "cartesia_api_key"),
    ("stt", "deepgram"): ("stt", "deepgram_api_key"),
    ("stt", "gnani"): ("stt", "gnani_api_key"),
    ("stt", "sarvam"): ("stt", "sarvam_api_key"),
    ("stt", "groq"): ("llm", "groq_api_key"),
    ("tts", "cartesia"): ("tts", "cartesia_api_key"),
    ("tts", "deepgram"): ("tts", "deepgram_api_key"),
    ("tts", "gnani"): ("tts", "gnani_api_key"),
    ("tts", "sarvam"): ("tts", "sarvam_api_key"),
    ("tts", "murf"): ("tts", "murf_api_key"),
    ("tts", "azure"): ("tts", "azure_speech_key"),
    ("tts", "openai"): ("llm", "openai_api_key"),
}

# Headers a tenant may not set: they would change how the request is routed or framed.
FORBIDDEN_HEADERS = {"host", "content-length", "content-type", "transfer-encoding", "connection", "upgrade", "te",
                     "proxy-authorization", "proxy-connection", "x-forwarded-for", "x-forwarded-host"}


class ExternalModelError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# --- organization provider keys ----------------------------------------------------------

def check_provider(kind: str, provider: str) -> Tuple[str, str]:
    kind, provider = (kind or "").lower(), (provider or "").lower()
    if (kind, provider) not in CREDENTIAL_FIELDS:
        raise ExternalModelError(f"'{provider}' is not a supported {kind} provider for your own key")
    return kind, provider


async def set_credential(db: AsyncSession, tenant_id: int, kind: str, provider: str, key: str, actor: str) -> ProviderCredential:
    kind, provider = check_provider(kind, provider)
    key = (key or "").strip()
    if len(key) < 8 or len(key) > 500 or any(c.isspace() for c in key):
        raise ExternalModelError("That does not look like an API key")
    try:
        token = secret_store.encrypt(key)
    except secret_store.SecretStoreError as exc:
        raise ExternalModelError(str(exc), 503)
    row = (await db.execute(select(ProviderCredential).where(
        ProviderCredential.tenant_id == tenant_id, ProviderCredential.kind == kind, ProviderCredential.provider == provider))).scalars().first()
    if row:
        row.secret_enc, row.last4, row.updated_at = token, secret_store.last4(key), datetime.utcnow()
    else:
        row = ProviderCredential(tenant_id=tenant_id, kind=kind, provider=provider, secret_enc=token, last4=secret_store.last4(key),
                                 created_by=actor, created_at=datetime.utcnow(), updated_at=datetime.utcnow())
        db.add(row)
    await db.flush()
    return row


async def load_org_credentials(db: AsyncSession, tenant_id: Optional[int]) -> Dict[Tuple[str, str], str]:
    """Decrypted keys for one organization. A key that cannot be decrypted is skipped (the platform key is used)."""
    if tenant_id is None:
        return {}
    out: Dict[Tuple[str, str], str] = {}
    for row in (await db.execute(select(ProviderCredential).where(ProviderCredential.tenant_id == tenant_id))).scalars().all():
        try:
            out[(row.kind, row.provider)] = secret_store.decrypt(row.secret_enc)
        except secret_store.SecretStoreError as exc:
            logger.warning("skipping unreadable credential %s/%s for org %s: %s", row.kind, row.provider, tenant_id, exc)
    return out


def apply_org_credentials(cfg: AppConfig, creds: Dict[Tuple[str, str], str]) -> AppConfig:
    """The call's configuration with the organization's own keys swapped in. Everything else is unchanged."""
    if not creds:
        return cfg
    updates: Dict[str, Dict[str, str]] = {}
    for (kind, provider), key in creds.items():
        section, field = CREDENTIAL_FIELDS[(kind, provider)]
        updates.setdefault(section, {})[field] = key
    parts = {name: dataclasses.replace(getattr(cfg, name), **fields) for name, fields in updates.items()}
    return dataclasses.replace(cfg, **parts)


# --- custom OpenAI-compatible endpoints ----------------------------------------------------

def _clean_headers(headers: Optional[Dict[str, str]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for name, value in (headers or {}).items():
        name, value = str(name).strip(), str(value).strip()
        if not name or not value or len(name) > 60 or len(value) > 500 or any(c in name + value for c in "\r\n"):
            raise ExternalModelError("Custom headers must have short names and values without line breaks")
        if name.lower() in FORBIDDEN_HEADERS:
            raise ExternalModelError(f"The header '{name}' cannot be set")
        out[name] = value
    if len(out) > 10:
        raise ExternalModelError("At most 10 custom headers are allowed")
    return out


async def save_endpoint(db: AsyncSession, actor: str, *, tenant_id: Optional[int], name: str, base_url: str, model: str,
                        api_key: Optional[str], headers: Optional[Dict[str, str]], supports_tools: bool,
                        acknowledge: bool, endpoint: Optional[ModelEndpoint] = None) -> ModelEndpoint:
    name, model = (name or "").strip(), (model or "").strip()
    if not name or not model:
        raise ExternalModelError("A name and a model are required")
    if not acknowledge:
        raise ExternalModelError("Confirm that call transcripts will be sent to this external service")
    try:
        url = await avalidate_external_url(base_url)
    except UnsafeURL as exc:
        raise ExternalModelError(str(exc))
    try:
        key_token = secret_store.encrypt(api_key.strip()) if api_key and api_key.strip() else None
        headers_token = secret_store.encrypt(_json(_clean_headers(headers))) if headers else None
    except secret_store.SecretStoreError as exc:
        raise ExternalModelError(str(exc), 503)
    if endpoint is None:
        endpoint = ModelEndpoint(tenant_id=tenant_id, created_by=actor, created_at=datetime.utcnow())
        db.add(endpoint)
    endpoint.name, endpoint.base_url, endpoint.model = name[:100], url, model[:200]
    if key_token is not None:                      # omitting the key on an update keeps the stored one
        endpoint.api_key_enc = key_token
    if headers is not None:
        endpoint.headers_enc = headers_token
    endpoint.supports_tools = bool(supports_tools)
    endpoint.acknowledged_by = actor
    endpoint.last_test_ok = None
    await db.flush()
    return endpoint


def _json(value: Any) -> str:
    import json
    return json.dumps(value, separators=(",", ":"))


def _decrypt_headers(row: ModelEndpoint) -> Dict[str, str]:
    if not row.headers_enc:
        return {}
    import json
    return json.loads(secret_store.decrypt(row.headers_enc))


async def visible_endpoints(db: AsyncSession, tenant_id: Optional[int], include_all: bool = False) -> List[ModelEndpoint]:
    """Enabled endpoints an organization may use: its own and the platform-wide ones."""
    q = select(ModelEndpoint).where(ModelEndpoint.enabled.is_(True)).order_by(ModelEndpoint.name)
    if not include_all:
        q = q.where((ModelEndpoint.tenant_id == tenant_id) | (ModelEndpoint.tenant_id.is_(None))) if tenant_id is not None \
            else q.where(ModelEndpoint.tenant_id.is_(None))
    return list((await db.execute(q)).scalars().all())


async def resolve_endpoint(db: AsyncSession, endpoint_id: int, tenant_id: Optional[int]) -> Optional[Dict[str, Any]]:
    """Everything needed to call an endpoint, decrypted, or None when it is unusable for this organization."""
    row = await db.get(ModelEndpoint, endpoint_id)
    if not row or not row.enabled or (row.tenant_id is not None and row.tenant_id != tenant_id):
        return None
    try:
        await avalidate_external_url(row.base_url)           # re-check: DNS may have changed since it was saved
        return {"id": row.id, "base_url": row.base_url, "model": row.model,
                "api_key": secret_store.decrypt(row.api_key_enc) if row.api_key_enc else "",
                "headers": _decrypt_headers(row), "supports_tools": bool(row.supports_tools)}
    except (UnsafeURL, secret_store.SecretStoreError) as exc:
        logger.warning("custom endpoint %s unusable: %s", endpoint_id, exc)
        return None


def _sanitise_error(text: str, secrets: List[str]) -> str:
    text = " ".join(str(text).split())
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text[:200]


async def test_endpoint(db: AsyncSession, row: ModelEndpoint, transport: Optional[httpx.AsyncBaseTransport] = None) -> Dict[str, Any]:
    """One tiny chat request. Records the outcome on the endpoint; never raises."""
    started = time.perf_counter()
    secrets: List[str] = []
    ok, error = False, ""
    try:
        url = await avalidate_external_url(row.base_url)
        key = secret_store.decrypt(row.api_key_enc) if row.api_key_enc else ""
        headers = _decrypt_headers(row)
        secrets = [key, *headers.values()]
        request_headers = {"Content-Type": "application/json", **headers}
        if key:
            request_headers["Authorization"] = f"Bearer {key}"
        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=4.0), follow_redirects=False, transport=transport) as client:
            response = await client.post(f"{url}/chat/completions", headers=request_headers, json={
                "model": row.model, "messages": [{"role": "user", "content": "Reply with the word ok."}], "max_tokens": 5})
        if response.status_code == 200 and response.json().get("choices"):
            ok = True
        else:
            error = f"HTTP {response.status_code}: {response.text}"
    except (UnsafeURL, secret_store.SecretStoreError, httpx.HTTPError, ValueError) as exc:
        error = f"{type(exc).__name__}: {exc}"
    elapsed = (time.perf_counter() - started) * 1000
    row.last_test_ok, row.last_test_ms = ok, round(elapsed, 1)
    row.last_test_error = None if ok else _sanitise_error(error, secrets)
    row.last_tested_at = datetime.utcnow()
    await db.flush()
    return {"ok": ok, "ms": row.last_test_ms, "error": row.last_test_error}
