import os
import re
import uuid
import logging
import tempfile
from typing import List, Optional

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .rag_engine import RAGEngine, SUPPORTED_EXTENSIONS, MAX_UPLOAD_BYTES
from .database import get_db
from .models import KnowledgeDocument
from .websocket_manager import dashboard_manager
from .tenancy import request_scope, system_tenant_id_cached

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

rag_engine = RAGEngine()


async def _warm_rag() -> None:
    if rag_engine.vector is not None:
        rag_engine._kick_sync()


router.add_event_handler("startup", _warm_rag)


def _safe_name(name: Optional[str]) -> str:
    base = os.path.basename((name or "").replace("\\", "/")) or "upload"
    return re.sub(r"[^A-Za-z0-9._ -]", "_", base)[:150]


async def _broadcast(payload: dict) -> None:
    try:
        await dashboard_manager.broadcast(payload)
    except Exception:
        pass


@router.get("/documents")
async def list_knowledge_documents():
    scope = request_scope()
    docs = rag_engine.list_documents(tenant_id=scope.tenant_id)
    return {"documents": docs, "total": len(docs)}


@router.post("/upload")
async def upload_knowledge_document(file: UploadFile = File(...), node_id: Optional[str] = Form(None)):
    scope = request_scope()
    filename = _safe_name(file.filename)
    ext = os.path.splitext(filename)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported file type '{ext or filename}'. Supported: {', '.join(SUPPORTED_EXTENSIONS)}")
    content = await file.read()
    if not content:
        raise HTTPException(400, "The uploaded file is empty.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File is too large (limit {MAX_UPLOAD_BYTES // (1024 * 1024)} MB).")

    fd, tmp_path = tempfile.mkstemp(suffix=ext)
    try:
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(content)
        doc_id = str(uuid.uuid4())
        try:
            owner = scope.tenant_id if scope.tenant_id is not None else system_tenant_id_cached()
            result = await rag_engine.ingest_document(tmp_path, doc_id, filename, node_id=node_id or None, tenant_id=owner)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            logger.error("Knowledge upload failed for %s: %s", filename, e, exc_info=True)
            raise HTTPException(500, "Could not index this document. Check the file and try again.")
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    doc = next((d for d in rag_engine.list_documents(tenant_id=scope.tenant_id) if d["id"] == doc_id), None)
    await _broadcast({"type": "knowledge_updated", "document": doc})
    return {"status": "success", "document_id": doc_id, "name": filename,
            "chunks": result["chunks"], "semantic": result["semantic"]}


@router.get("/documents/{doc_id}")
async def read_knowledge_document(doc_id: str):
    """The text of one document, for reading in the console."""
    doc = rag_engine.read_document(doc_id, tenant_id=request_scope().tenant_id)
    if doc is None:
        raise HTTPException(404, "Document not found.")             # another company's document answers the same
    return doc


class DocumentText(BaseModel):
    content: str


@router.put("/documents/{doc_id}")
async def edit_knowledge_document(doc_id: str, body: DocumentText):
    """Replace the text of an uploaded document (what Architect wrote, or a file's extracted text). It is indexed again at once, so the
    agents that read it answer from the new text from then on."""
    try:
        result = await rag_engine.update_document_text(doc_id, body.content, tenant_id=request_scope().tenant_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if result is None:
        raise HTTPException(404, "Document not found.")
    doc = next((d for d in rag_engine.list_documents(tenant_id=request_scope().tenant_id) if d["id"] == doc_id), None)
    await _broadcast({"type": "knowledge_updated", "document": doc})
    return {"status": "saved", "document_id": doc_id, "name": result["name"], "chunks": result["chunks"], "semantic": result["semantic"]}


@router.delete("/documents/{doc_id}")
async def delete_knowledge_document(doc_id: str, db: AsyncSession = Depends(get_db)):
    scope = request_scope()
    removed = await rag_engine.delete_document(doc_id, tenant_id=scope.tenant_id)
    if not removed:
        # Documents uploaded before the registry existed were known to the UI by a database UUID.
        row = (await db.execute(select(KnowledgeDocument).where(KnowledgeDocument.id == doc_id))).scalars().first()
        if row:
            removed = await rag_engine.delete_document(row.name, tenant_id=scope.tenant_id)
            await db.delete(row)
            await db.commit()
    if not removed:
        raise HTTPException(404, "Document not found.")
    await _broadcast({"type": "knowledge_deleted", "document_id": doc_id})
    return {"status": "deleted", "document_id": doc_id}


class SearchRequest(BaseModel):
    query: str
    node_id: Optional[str] = None
    doc_ids: Optional[List[str]] = None
    top_k: int = 3


@router.post("/search")
async def test_knowledge_search(req: SearchRequest):
    scope = request_scope()
    """Run the live retrieval path (keyword + semantic) and show what each leg contributed."""
    out = await rag_engine.search_ranked(req.query, node_id=req.node_id, node_docs=req.doc_ids,
                                         top_k=max(1, min(req.top_k, 10)), tenant_id=scope.tenant_id)
    for r in out["results"]:
        r["text"] = r["text"][:500]
    return out


@router.get("/status")
async def knowledge_status():
    scope = request_scope()
    return rag_engine.stats(tenant_id=scope.tenant_id)


@router.post("/reindex")
async def reindex_knowledge():
    """Re-embed every document into the vector store (use after changing the embedding model or Qdrant)."""
    if request_scope().restricted:                       # rebuilds the whole platform index: not a tenant's call
        raise HTTPException(403, "Only platform staff can do that.")
    return await rag_engine.reindex_vectors()
