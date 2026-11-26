"""An organization's external vector database: configure, test, index, remove."""
import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import secret_store
from . import vector_stores as vs
from .access import User, audit, require_tenant_admin, scope_tenant
from .database import get_db
from .models import VectorStoreConfig
from .rag_engine import RAGEngine

router = APIRouter(prefix="/api/vector-stores", tags=["vector-stores"])


class StoreBody(BaseModel):
    tenant_id: Optional[int] = None
    provider: str = "qdrant"
    url: str = Field(max_length=512)
    api_key: Optional[str] = Field(default=None, max_length=600, repr=False)
    collection: str = Field(max_length=120)
    mode: str = "index"
    text_field: Optional[str] = Field(default=None, max_length=80)
    source_field: Optional[str] = Field(default=None, max_length=80)
    enabled: bool = True


def _engine():
    return RAGEngine()


def _dim() -> Optional[int]:
    embedder = getattr(_engine().vector, "embedder", None)
    return getattr(embedder, "dim", None)


def _fail(exc: vs.VectorStoreError):
    raise HTTPException(status_code=exc.status, detail=str(exc))


async def _row(db: AsyncSession, tenant_id: int) -> Optional[VectorStoreConfig]:
    return (await db.execute(select(VectorStoreConfig).where(VectorStoreConfig.tenant_id == tenant_id))).scalars().first()


@router.get("")
async def get_store(tenant_id: Optional[int] = Query(default=None), user: User = Depends(require_tenant_admin),
                    db: AsyncSession = Depends(get_db)):
    target = scope_tenant(user, tenant_id)
    row = await _row(db, target)
    return {"config": row.to_dict() if row else None, "embedding_dim": _dim(),
            "encryption_configured": secret_store.is_configured(), "providers": list(vs.PROVIDERS), "modes": list(vs.MODES)}


@router.put("")
async def put_store(body: StoreBody, user: User = Depends(require_tenant_admin), db: AsyncSession = Depends(get_db)):
    target = scope_tenant(user, body.tenant_id)
    try:
        row = await vs.save_config(db, user.email, target, provider=body.provider, url=body.url, api_key=body.api_key,
                                   collection=body.collection, mode=body.mode, text_field=body.text_field,
                                   source_field=body.source_field, enabled=body.enabled)
    except vs.VectorStoreError as exc:
        await db.rollback()
        _fail(exc)
    await audit(db, user.email, "vector_store.set", tenant_id=target, target=row.collection,
                detail={"mode": row.mode, "url": row.url})                              # never the key
    await db.commit()
    _engine().org_stores.invalidate(target)
    return row.to_dict()


@router.delete("")
async def delete_store(tenant_id: Optional[int] = Query(default=None), user: User = Depends(require_tenant_admin),
                       db: AsyncSession = Depends(get_db)):
    target = scope_tenant(user, tenant_id)
    row = await _row(db, target)
    if not row:
        raise HTTPException(status_code=404, detail="No external vector store is configured.")
    await audit(db, user.email, "vector_store.delete", tenant_id=target, target=row.collection)
    await db.delete(row)
    await db.commit()
    _engine().org_stores.invalidate(target)
    return {"deleted": True}


@router.post("/test")
async def test_store(tenant_id: Optional[int] = Query(default=None), user: User = Depends(require_tenant_admin),
                     db: AsyncSession = Depends(get_db)):
    target = scope_tenant(user, tenant_id)
    row = await _row(db, target)
    if not row:
        raise HTTPException(status_code=404, detail="No external vector store is configured.")
    try:
        result = await vs.test_config(db, row, _dim())
    except secret_store.SecretStoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    await audit(db, user.email, "vector_store.test", tenant_id=target, target=row.collection, detail={"ok": result["ok"]})
    await db.commit()
    _engine().org_stores.invalidate(target)
    return result


@router.post("/reindex")
async def reindex_store(tenant_id: Optional[int] = Query(default=None), force: bool = False,
                        user: User = Depends(require_tenant_admin), db: AsyncSession = Depends(get_db)):
    """Index mode only: copy our knowledge vectors into the organization's store."""
    target = scope_tenant(user, tenant_id)
    row = await _row(db, target)
    if not row:
        raise HTTPException(status_code=404, detail="No external vector store is configured.")
    if row.mode != "index":
        raise HTTPException(status_code=409, detail="Read-only stores are never written to.")
    engine = _engine()
    resolved = await engine.org_stores.for_org(target)
    if resolved is None or resolved.index_store is None:
        raise HTTPException(status_code=409, detail="The store is not usable right now; run the connection test first.")
    try:
        counts = await asyncio.wait_for(engine.sync_into(resolved.index_store, force=force, tenant_id=target), timeout=300)
    except Exception as exc:
        await audit(db, user.email, "vector_store.reindex_failed", tenant_id=target, target=row.collection, detail={"error": type(exc).__name__})
        await db.commit()
        raise HTTPException(status_code=502, detail=f"Indexing failed: {type(exc).__name__}")
    await audit(db, user.email, "vector_store.reindex", tenant_id=target, target=row.collection, detail=counts)
    await db.commit()
    engine.org_stores.invalidate(target)
    return counts
