"""An organization's own vector database for knowledge retrieval.

Two modes
  index      we write our knowledge vectors into the organization's Qdrant (collection "<name>_<dim>") and search them
             there; the passage text still comes from our own documents.
  read_only  the organization already has an index. We only query it and use the text stored in each point's payload
             (text_field, optional source_field). We never write, create or delete anything in it.

A store that is slow or failing must never slow a call: every search has a short time budget, repeated failures open a
circuit breaker for a cool-down, and the caller (retrieval) simply carries on with the platform store.
"""
import asyncio
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import secret_store
from .database import async_session
from .models import VectorStoreConfig
from .net_guard import UnsafeURL, avalidate_external_url

logger = logging.getLogger(__name__)

MODES = ("index", "read_only")
PROVIDERS = ("qdrant",)


class VectorStoreError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class CircuitBreaker:
    """Open after `threshold` failures in a row; allow one probe again after `cooldown` seconds."""

    def __init__(self, threshold: int = 3, cooldown: float = 30.0, clock: Callable[[], float] = time.monotonic):
        self.threshold, self.cooldown, self._clock = threshold, cooldown, clock
        self.failures = 0
        self.opened_at: Optional[float] = None

    def allow(self) -> bool:
        if self.opened_at is None:
            return True
        if self._clock() - self.opened_at >= self.cooldown:
            self.opened_at = None            # half-open: let the next call try
            self.failures = self.threshold - 1
            return True
        return False

    def record(self, ok: bool) -> None:
        if ok:
            self.failures, self.opened_at = 0, None
            return
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = self._clock()


def make_client(url: str, api_key: str):
    from qdrant_client import AsyncQdrantClient
    return AsyncQdrantClient(url=url, api_key=api_key or None, timeout=5)


@dataclass
class Resolved:
    tenant_id: int
    mode: str
    config: Dict[str, Any]
    client: Any
    index_store: Any = None            # QdrantVectorStore (index mode)
    ready: bool = False
    breaker: Optional[CircuitBreaker] = None


async def check_connection(url: str, api_key: str, collection: str, mode: str, dim: Optional[int],
                           client_factory: Optional[Callable] = None) -> Dict[str, Any]:
    """Connect, find the collection and compare its vector size with our embedding model. Never raises."""
    started = time.perf_counter()
    out: Dict[str, Any] = {"ok": False, "error": None, "vector_size": None, "collection_exists": None, "will_create": False}
    client = None
    try:
        url = await avalidate_external_url(url)
        client = (client_factory or make_client)(url, api_key)
        existing = {c.name for c in (await client.get_collections()).collections}
        name = collection if mode == "read_only" else f"{collection}_{dim}" if dim else collection
        out["collection_exists"] = name in existing
        out["collection_name"] = name
        if name in existing:
            info = await client.get_collection(name)
            vectors = info.config.params.vectors
            size = getattr(vectors, "size", None)
            if size is None and isinstance(vectors, dict):                # named vectors: not supported
                out["error"] = "The collection uses named vectors, which are not supported"
            out["vector_size"] = size
            if dim and size and size != dim:
                out["error"] = f"The collection holds {size}-dimension vectors but our embedding model produces {dim}"
        elif mode == "read_only":
            out["error"] = f"Collection '{collection}' does not exist, and read-only mode never creates one"
        else:
            out["will_create"] = True
        out["ok"] = out["error"] is None
    except UnsafeURL as exc:
        out["error"] = str(exc)
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception:
                pass
    out["ms"] = round((time.perf_counter() - started) * 1000, 1)
    if api_key and out.get("error"):
        out["error"] = out["error"].replace(api_key, "***")
    return out


class VectorStoreRegistry:
    """Resolves (and caches for a short time) the vector store configured for an organization."""

    def __init__(self, embedder_getter: Callable[[], Any], ttl: float = 60.0, client_factory: Optional[Callable] = None,
                 session_factory: Optional[Callable] = None):
        self._embedder = embedder_getter
        self.ttl = ttl
        self._client_factory = client_factory
        self._session = session_factory
        self._cache: Dict[int, Tuple[float, Optional[Resolved]]] = {}
        self._breakers: Dict[int, CircuitBreaker] = {}
        self.external_timeout = int(os.getenv("RAG_EXTERNAL_TIMEOUT_MS", "600")) / 1000.0

    def invalidate(self, tenant_id: Optional[int] = None) -> None:
        if tenant_id is None:
            self._cache.clear()
        else:
            self._cache.pop(tenant_id, None)

    def _breaker(self, tenant_id: int) -> CircuitBreaker:
        return self._breakers.setdefault(tenant_id, CircuitBreaker())

    async def for_org(self, tenant_id: Optional[int]) -> Optional[Resolved]:
        if tenant_id is None:
            return None
        hit = self._cache.get(tenant_id)
        if hit and time.monotonic() - hit[0] < self.ttl:
            return hit[1]
        resolved: Optional[Resolved] = None
        try:
            async with (self._session or async_session)() as db:                # resolved at call time so tests can swap the database
                row = (await db.execute(select(VectorStoreConfig).where(
                    VectorStoreConfig.tenant_id == tenant_id, VectorStoreConfig.enabled.is_(True)))).scalars().first()
                if row:
                    resolved = await self._build(row)
        except Exception as exc:
            logger.warning("vector store lookup failed for org %s: %s", tenant_id, exc)
        self._cache[tenant_id] = (time.monotonic(), resolved)
        return resolved

    async def _build(self, row: VectorStoreConfig) -> Optional[Resolved]:
        embedder = self._embedder()
        if embedder is None:
            return None
        try:
            url = await avalidate_external_url(row.url)
            key = secret_store.decrypt(row.api_key_enc) if row.api_key_enc else ""
        except (UnsafeURL, secret_store.SecretStoreError) as exc:
            logger.warning("vector store of org %s is unusable: %s", row.tenant_id, exc)
            return None
        client = (self._client_factory or make_client)(url, key)
        config = row.to_dict()
        resolved = Resolved(tenant_id=row.tenant_id, mode=row.mode, config=config, client=client, breaker=self._breaker(row.tenant_id))
        if row.mode == "index":
            from .rag_engine import QdrantVectorStore
            dim = int(getattr(embedder, "dim", None) or row.dim or 384)
            resolved.index_store = QdrantVectorStore(embedder, collection=row.collection, dim=dim, client=client)
            try:
                resolved.ready = (await client.count(resolved.index_store.collection)).count > 0
            except Exception:
                resolved.ready = False
        else:
            resolved.ready = True
        return resolved

    # -- queries used by retrieval (all bounded in time, all failure-tolerant) -------------------------

    async def semantic_ids(self, tenant_id: Optional[int], query: str, limit: int, node_id: Optional[str],
                           node_docs: Optional[List[str]], min_score: float) -> Optional[List[Tuple[str, float]]]:
        """Index mode: chunk ids from the organization's store, or None when it cannot be used right now."""
        resolved = await self.for_org(tenant_id)
        if not resolved or resolved.mode != "index" or not resolved.ready or not resolved.breaker.allow():
            return None
        try:
            found = await asyncio.wait_for(
                resolved.index_store.search(query, limit, node_id, node_docs, min_score), self.external_timeout)
            resolved.breaker.record(True)
            return found
        except Exception as exc:
            resolved.breaker.record(False)
            logger.info("organization vector store (index) skipped: %s", type(exc).__name__)
            return None

    async def passages(self, tenant_id: Optional[int], query: str, limit: int, min_score: float) -> List[Dict[str, Any]]:
        """Read-only mode: passages straight from the organization's index. Empty list on any problem."""
        resolved = await self.for_org(tenant_id)
        if not resolved or resolved.mode != "read_only" or not resolved.breaker.allow():
            return []
        cfg = resolved.config
        embedder = self._embedder()
        try:
            async def run() -> List[Dict[str, Any]]:
                vector = await embedder.embed_query(query)
                response = await resolved.client.query_points(
                    cfg["collection"], query=vector, limit=limit, score_threshold=min_score, with_payload=True)
                out = []
                for point in response.points:
                    payload = point.payload or {}
                    text = str(payload.get(cfg.get("text_field") or "text") or "").strip()
                    if text:
                        source = str(payload.get(cfg.get("source_field") or "") or "") if cfg.get("source_field") else ""
                        out.append({"text": text[:1500], "source": source[:120], "score": float(point.score)})
                return out
            found = await asyncio.wait_for(run(), self.external_timeout)
            resolved.breaker.record(True)
            return found
        except Exception as exc:
            resolved.breaker.record(False)
            logger.info("organization vector store (read-only) skipped: %s", type(exc).__name__)
            return []


# -- configuration (used by the API) -------------------------------------------------------------

async def save_config(db: AsyncSession, actor: str, tenant_id: int, *, provider: str, url: str, api_key: Optional[str],
                      collection: str, mode: str, text_field: Optional[str], source_field: Optional[str],
                      enabled: bool = True) -> VectorStoreConfig:
    provider, mode = (provider or "qdrant").lower(), (mode or "index").lower()
    if provider not in PROVIDERS:
        raise VectorStoreError(f"Supported vector databases: {', '.join(PROVIDERS)}")
    if mode not in MODES:
        raise VectorStoreError("Mode must be 'index' or 'read_only'")
    collection = (collection or "").strip()
    if not collection or len(collection) > 120 or not all(c.isalnum() or c in "-_." for c in collection):
        raise VectorStoreError("Collection names use letters, digits, dash, underscore and dot only")
    if mode == "read_only" and not (text_field or "").strip():
        raise VectorStoreError("Read-only mode needs the name of the payload field that holds the passage text")
    try:
        url = await avalidate_external_url(url)
    except UnsafeURL as exc:
        raise VectorStoreError(str(exc))
    try:
        token = secret_store.encrypt(api_key.strip()) if api_key and api_key.strip() else None
    except secret_store.SecretStoreError as exc:
        raise VectorStoreError(str(exc), 503)
    row = (await db.execute(select(VectorStoreConfig).where(VectorStoreConfig.tenant_id == tenant_id))).scalars().first()
    if row is None:
        row = VectorStoreConfig(tenant_id=tenant_id, created_by=actor, created_at=datetime.utcnow())
        db.add(row)
    row.provider, row.url, row.collection, row.mode = provider, url, collection, mode
    row.text_field = (text_field or "").strip() or None
    row.source_field = (source_field or "").strip() or None
    if token is not None:
        row.api_key_enc = token
    row.enabled = bool(enabled)
    row.last_test_ok = None
    await db.flush()
    return row


async def test_config(db: AsyncSession, row: VectorStoreConfig, dim: Optional[int],
                      client_factory: Optional[Callable] = None) -> Dict[str, Any]:
    key = secret_store.decrypt(row.api_key_enc) if row.api_key_enc else ""
    result = await check_connection(row.url, key, row.collection, row.mode, dim, client_factory=client_factory)
    row.last_test_ok, row.last_test_ms = result["ok"], result["ms"]
    row.last_test_error = (result.get("error") or None)
    row.last_tested_at = datetime.utcnow()
    row.dim = dim or row.dim
    await db.flush()
    return result
