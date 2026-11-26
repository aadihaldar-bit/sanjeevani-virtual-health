"""A storage bucket for each tenant, created automatically when the tenant is created.

Turned on with AUTO_CREATE_TENANT_BUCKETS=true (off by default: it needs a cloud permission the service account may
not have yet). Creating a tenant never fails because of storage: the bucket is made in the background, the outcome is
kept on the tenant (settings_json["storage"]) and a super admin can retry. Each bucket is private (uniform access,
public access prevented), labelled with its tenant, and used for that tenant's call logs. The platform's own tenant
keeps the shared bucket.

Environment
  AUTO_CREATE_TENANT_BUCKETS   true to create buckets automatically
  TENANT_BUCKET_PREFIX         name prefix, default "stl-voicebot"
  TENANT_BUCKET_LOCATION       region, default "us-central1"

The service account needs permission to create buckets (storage.buckets.create and storage.buckets.get, for example
through roles/storage.admin on the project). Whoever creates a bucket owns it, so no further grants are needed.
"""
import asyncio
import logging
import os
import re
import time
import uuid
from datetime import datetime
from typing import Any, Dict, Optional, Set

from sqlalchemy import select

logger = logging.getLogger(__name__)

READY, FAILED, CREATING, DISABLED = "ready", "failed", "creating", "disabled"
MAX_NAME_ATTEMPTS = 3
_NAME_VALID = re.compile(r"^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$")

_tasks: Set[asyncio.Task] = set()
_in_flight: Set[int] = set()
_cache: Dict[int, tuple] = {}          # tenant id -> (expires_at, bucket or None)
_CACHE_SECONDS = 60


def auto_create_enabled() -> bool:
    return os.getenv("AUTO_CREATE_TENANT_BUCKETS", "false").strip().lower() == "true"


def _prefix() -> str:
    return re.sub(r"[^a-z0-9-]+", "-", os.getenv("TENANT_BUCKET_PREFIX", "stl-voicebot").strip().lower()).strip("-") or "stl-voicebot"


def _location(chosen: Optional[str] = None) -> str:
    """The region for a new bucket: the company's own choice (settings.bucket_location) when it has one, else the server default."""
    return (chosen or "").strip() or os.getenv("TENANT_BUCKET_LOCATION", "us-central1").strip() or "us-central1"


# Regions an administrator may pick for a company (data residency). Anything else is refused.
BUCKET_REGIONS = {
    "me-central1": "Doha, Qatar", "me-central2": "Dammam, Saudi Arabia", "me-west1": "Tel Aviv, Israel",
    "europe-west1": "Belgium", "europe-west3": "Frankfurt, Germany", "europe-west4": "Netherlands", "europe-west2": "London, UK",
    "asia-south1": "Mumbai, India", "asia-south2": "Delhi, India", "asia-southeast1": "Singapore", "us-central1": "Iowa, USA",
}


def make_bucket_name(slug: str, tenant_id: int, suffix: Optional[str] = None) -> str:
    """A valid, globally unlikely bucket name: <prefix>-<slug>-<tenant id>-<random>, at most 63 characters."""
    tail = f"-{tenant_id}-{suffix or uuid.uuid4().hex[:6]}"
    base = re.sub(r"[^a-z0-9-]+", "-", (slug or "tenant").lower()).strip("-") or "tenant"
    head = f"{_prefix()}-"
    room = 63 - len(head) - len(tail)
    name = f"{head}{base[:max(room, 1)].strip('-') or 'tenant'}{tail}"
    name = name.replace("goog", "gcp")                  # names may not start with or contain "goog"/"google" lookalikes
    if not _NAME_VALID.match(name):
        raise ValueError(f"cannot build a valid bucket name from {slug!r}")
    return name


def _client():
    """The storage client the rest of the platform already uses (same credentials)."""
    from .event_logger import event_logger
    return event_logger._get_gcs_storage_client()


def _create_bucket_sync(client, name: str, tenant_id: int, location: Optional[str] = None):
    bucket = client.bucket(name)
    bucket.iam_configuration.uniform_bucket_level_access_enabled = True
    bucket.iam_configuration.public_access_prevention = "enforced"
    bucket.labels = {"tenant_id": str(tenant_id), "managed_by": "stl-voicebot"}
    return client.create_bucket(bucket, location=_location(location))


def _explain(exc: Exception) -> str:
    kind = type(exc).__name__
    if kind in ("Forbidden", "PermissionDenied", "Unauthorized"):
        return ("The service account is not allowed to create buckets. Grant it storage.buckets.create "
                "(for example roles/storage.admin on the project), then press Retry.")
    if kind == "ImportError":
        return "google-cloud-storage is not installed on the server."
    return f"{kind}: {str(exc)[:160]}"


async def _save(tenant_id: int, **storage) -> Dict[str, Any]:
    from .database import async_session
    from .models import Tenant
    async with async_session() as db:
        tenant = await db.get(Tenant, tenant_id)
        if not tenant:
            return {}
        current = dict((tenant.settings_json or {}).get("storage") or {})
        current.update(storage, updated_at=datetime.utcnow().isoformat())
        tenant.settings_json = {**(tenant.settings_json or {}), "storage": current}        # a new dict so the change is saved
        if storage.get("bucket"):
            tenant.gcs_bucket = storage["bucket"]
        await db.commit()
        _cache.pop(tenant_id, None)
        return current


async def provision_for_tenant(tenant_id: int, force: bool = False, actor: str = "system") -> Dict[str, Any]:
    """Create the tenant's bucket if it does not have one. Safe to call twice; never raises."""
    from .database import async_session
    from .models import Tenant
    try:
        async with async_session() as db:
            tenant = await db.get(Tenant, tenant_id)
            if not tenant:
                return {"status": FAILED, "error": "Tenant not found."}
            if tenant.is_system:
                return {"status": "skipped", "reason": "The platform tenant uses the shared bucket."}
            storage = dict((tenant.settings_json or {}).get("storage") or {})
            slug = tenant.slug
            chosen_region = (tenant.settings_json or {}).get("bucket_location")
            if tenant.gcs_bucket and storage.get("status") == READY and not force:
                return storage
        if not auto_create_enabled():
            return await _save(tenant_id, status=DISABLED, error="Automatic bucket creation is turned off (AUTO_CREATE_TENANT_BUCKETS).")
        if tenant_id in _in_flight:
            return {"status": CREATING}
        _in_flight.add(tenant_id)
        try:
            await _save(tenant_id, status=CREATING, error=None)
            client = await asyncio.to_thread(_client)
            last_error: Optional[Exception] = None
            for attempt in range(MAX_NAME_ATTEMPTS):
                name = make_bucket_name(slug, tenant_id)
                try:
                    await asyncio.to_thread(_create_bucket_sync, client, name, tenant_id, chosen_region)
                except Exception as exc:                                    # noqa: BLE001
                    last_error = exc
                    if type(exc).__name__ == "Conflict":                    # the name is taken somewhere: try another
                        logger.info("[TenantStorage] bucket name %s is taken, retrying", name)
                        continue
                    break
                saved = await _save(tenant_id, status=READY, bucket=name, error=None, location=_location(chosen_region))
                await _audit(actor, tenant_id, "storage.bucket_created", name)
                logger.info("[TenantStorage] created gs://%s for tenant %s", name, tenant_id)
                return saved
            message = _explain(last_error) if last_error else "Could not create the bucket."
            logger.warning("[TenantStorage] bucket for tenant %s failed: %s", tenant_id, message)
            await _audit(actor, tenant_id, "storage.bucket_failed", None, {"error": message})
            return await _save(tenant_id, status=FAILED, error=message)
        finally:
            _in_flight.discard(tenant_id)
    except Exception as exc:                                                # noqa: BLE001  never break the caller
        logger.warning("[TenantStorage] provisioning crashed for tenant %s: %s", tenant_id, exc)
        return {"status": FAILED, "error": _explain(exc)}


async def _audit(actor: str, tenant_id: int, action: str, target: Optional[str], detail: Optional[dict] = None) -> None:
    try:
        from .access import audit
        from .database import async_session
        async with async_session() as db:
            await audit(db, actor, action, tenant_id=tenant_id, target=target, detail=detail)
            await db.commit()
    except Exception as exc:                                                # noqa: BLE001
        logger.debug("[TenantStorage] audit skipped: %s", exc)


def start_provisioning(tenant_id: int, actor: str = "system") -> Optional[asyncio.Task]:
    """Fire and forget: called right after a tenant is created. Does nothing when the feature is off."""
    if not auto_create_enabled():
        return None
    task = asyncio.get_running_loop().create_task(provision_for_tenant(tenant_id, actor=actor))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


async def wait_idle() -> None:
    """Wait for background provisioning (used by tests and by shutdown)."""
    loop = asyncio.get_running_loop()
    for stale in [x for x in _tasks if x.get_loop() is not loop]:        # left over from a finished event loop
        _tasks.discard(stale)
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


async def register_existing_bucket(tenant_id: int, bucket_name: str, actor: str) -> Dict[str, Any]:
    """Use a bucket someone already created: prove we can write to it, then record it. Raises ValueError with a reason."""
    name = (bucket_name or "").strip().lower()
    if not _NAME_VALID.match(name):
        raise ValueError("That is not a valid bucket name.")

    def probe() -> None:
        blob = _client().bucket(name).blob(f".stl-probe-{uuid.uuid4().hex[:8]}")
        blob.upload_from_string("ok")
        blob.delete()

    try:
        await asyncio.to_thread(probe)
    except Exception as exc:                                                # noqa: BLE001
        raise ValueError(f"Could not write to that bucket ({type(exc).__name__}). Check it exists and the service account may write to it.")
    saved = await _save(tenant_id, status=READY, bucket=name, error=None, registered=True)
    await _audit(actor, tenant_id, "storage.bucket_registered", name)
    return saved


async def bucket_for_tenant(tenant_id: Optional[int]) -> Optional[str]:
    """The tenant's own bucket when it has a working one, else None (use the shared bucket). Cached briefly."""
    if tenant_id is None:
        return None
    hit = _cache.get(tenant_id)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    bucket = None
    try:
        from .database import async_session
        from .models import Tenant
        async with async_session() as db:
            tenant = (await db.execute(select(Tenant).where(Tenant.id == tenant_id))).scalars().first()
            if tenant and tenant.gcs_bucket and ((tenant.settings_json or {}).get("storage") or {}).get("status") == READY:
                bucket = tenant.gcs_bucket
    except Exception as exc:                                                # noqa: BLE001
        logger.debug("[TenantStorage] lookup failed: %s", exc)
    _cache[tenant_id] = (time.monotonic() + _CACHE_SECONDS, bucket)
    return bucket
