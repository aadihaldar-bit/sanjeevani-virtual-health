"""
Hybrid RAG engine for voice workflows.

  keyword leg : in-memory BM25 (with IDF) over section-aware chunks, ~1 ms
  semantic leg: Qdrant vector search over Gemini embeddings, hard time budget
  fusion      : reciprocal rank fusion; if the semantic leg is slow or down the
                answer is the keyword result, so a call never waits on it.

Documents live in knowledge_base/ (extracted text, one file per document) plus
_registry.json (original filename, node scope, upload time). Files that exist
in the directory without a registry entry are treated as built-in documents.
"""

import asyncio
import html
import json
import logging
import math
import os
import re
import threading
import time
import uuid
from collections import Counter, OrderedDict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .config import AppConfig, get_config
from .interfaces import BaseRAGEngine

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = (".pdf", ".docx", ".md", ".txt", ".html", ".htm", ".csv")
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_EDIT_CHARS = 200_000                       # the longest text a person may save over a document
REGISTRY_FILE = "_registry.json"

_TOKEN_RE = re.compile(r"\b[a-zA-Z0-9_-]{2,}\b")
_STOP_WORDS = {"the", "is", "at", "which", "on", "a", "an", "and", "or", "in", "to", "for", "of", "my", "so",
               "have", "am", "with", "like", "can", "you", "me", "this", "that", "what", "how", "do", "does",
               "it", "i", "are", "be", "your", "about", "please", "tell"}
_SMALL_TALK_WORDS = {
    "yes", "yeah", "yep", "yup", "no", "nope", "ok", "okay", "sure", "thanks", "thank", "you", "hello", "hi",
    "hey", "bye", "goodbye", "correct", "right", "fine", "hmm", "uh", "um", "go", "ahead", "thats", "that", "all",
    "nothing", "else", "please", "alright", "great", "good", "cool", "perfect", "sounds", "works", "for", "me",
    "can", "hear", "i", "do", "it", "is", "so", "well", "got", "sorry", "pardon", "again", "what", "there",
    "morning", "afternoon", "evening", "have", "day", "nice", "to", "meet", "welcome", "much", "very",
}


# ─── Text extraction & chunking ─────────────────────────────────────────────

def extract_text(path: str, filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        text = "\n\n".join((p.extract_text() or "") for p in PdfReader(path).pages)
    elif ext == ".docx":
        import docx
        document = docx.Document(path)
        parts = []
        for para in document.paragraphs:
            t = para.text.strip()
            if not t:
                continue
            style = (para.style.name or "") if para.style is not None else ""
            parts.append(f"## {t}" if style.startswith("Heading") else t)
        for table in document.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
        text = "\n\n".join(parts)
    elif ext in (".html", ".htm"):
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.read()
        raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
        raw = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>", "\n", raw)
        raw = re.sub(r"(?i)</h\d>", "\n\n", raw)
        text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    elif ext in (".md", ".txt", ".csv"):
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
    else:
        raise ValueError(f"Unsupported file type '{ext or filename}'. Supported: {', '.join(SUPPORTED_EXTENSIONS)}")

    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) < 20:
        raise ValueError("No readable text found in this file (is it a scanned or image-only document?).")
    return text


def chunk_text(text: str, max_chars: int = 900, overlap: int = 120) -> List[Tuple[str, str]]:
    """Split on markdown headings, then pack paragraphs up to max_chars. Returns (section, text)."""
    out: List[Tuple[str, str]] = []
    for i, sec in enumerate(re.split(r"(?=^#{1,3}\s+)", text, flags=re.M)):
        sec = sec.strip()
        if len(sec) < 20:
            continue
        m = re.match(r"^#{1,3}\s+(.+)$", sec, re.M)
        title = m.group(1).strip() if m else f"Section {i + 1}"
        if len(sec) <= max_chars:
            out.append((title, sec))
            continue
        buf = ""
        for para in re.split(r"\n\s*\n", sec):
            if len(para) > max_chars:
                if buf:
                    out.append((title, buf))
                    buf = ""
                start = 0
                while start < len(para):
                    out.append((title, para[start:start + max_chars]))
                    start += max_chars - overlap
            elif len(buf) + len(para) + 2 <= max_chars:
                buf = f"{buf}\n\n{para}".strip()
            else:
                out.append((title, buf))
                buf = para
        if buf:
            out.append((title, buf))
    return out


def _tokens(text: str) -> List[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


# ─── Embeddings & vector store ──────────────────────────────────────────────

class EmbeddingError(RuntimeError):
    pass


class GeminiEmbedder:
    """Gemini embeddings over REST (async). Raises EmbeddingError; never returns fake vectors."""

    slug = "gemini"
    default_min_similarity = 0.5

    def __init__(self, api_key: str, model: str = "gemini-embedding-001", dim: int = 768):
        self.api_key = api_key
        self.model = model
        self.dim = dim
        self._cache: "OrderedDict[str, List[float]]" = OrderedDict()

    async def _embed(self, texts: List[str], task_type: str, timeout: float) -> List[List[float]]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:batchEmbedContents"
        body = {"requests": [{
            "model": f"models/{self.model}",
            "content": {"parts": [{"text": t}]},
            "taskType": task_type,
            "outputDimensionality": self.dim,
        } for t in texts]}
        last = ""
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(url, json=body, headers={"x-goog-api-key": self.api_key})
                if resp.status_code == 200:
                    vectors = [e["values"] for e in resp.json().get("embeddings", [])]
                    if len(vectors) != len(texts):
                        raise EmbeddingError("embedding count mismatch")
                    return vectors
                last = f"HTTP {resp.status_code}: {resp.text[:150]}"
                if resp.status_code not in (429, 500, 502, 503):
                    break
            except httpx.HTTPError as e:
                last = str(e)
            await asyncio.sleep(0.5 * (attempt + 1))
        raise EmbeddingError(last or "embedding failed")

    async def embed_documents(self, texts: List[str]) -> List[List[float]]:
        out: List[List[float]] = []
        for i in range(0, len(texts), 50):
            out.extend(await self._embed(texts[i:i + 50], "RETRIEVAL_DOCUMENT", 30.0))
        return out

    async def embed_query(self, text: str) -> List[float]:
        key = text.strip().lower()
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        vec = (await self._embed([text], "RETRIEVAL_QUERY", 5.0))[0]
        self._cache[key] = vec
        if len(self._cache) > 256:
            self._cache.popitem(last=False)
        return vec


class FastEmbedEmbedder:
    """Local ONNX embeddings (no key, no quota, ~3 ms per query on CPU). Loads in the background."""

    slug = "bge_small"
    default_min_similarity = 0.5

    def __init__(self, model: str = "BAAI/bge-small-en-v1.5", dim: int = 384, cache_dir: Optional[str] = None):
        self.model = model
        self.dim = dim
        self.cache_dir = cache_dir
        self._model: Any = None
        self._lock = threading.Lock()
        self._cache: "OrderedDict[str, List[float]]" = OrderedDict()

    def _load(self) -> None:
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding
                self._model = TextEmbedding(self.model, cache_dir=self.cache_dir)

    async def warmup(self) -> None:
        await asyncio.to_thread(self._load)

    async def embed_documents(self, texts: List[str]) -> List[List[float]]:
        def run() -> List[List[float]]:
            self._load()
            return [v.tolist() for v in self._model.embed(texts)]
        return await asyncio.to_thread(run)

    async def embed_query(self, text: str) -> List[float]:
        key = text.strip().lower()
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        if self._model is None:
            raise EmbeddingError("embedding model is still loading")
        vec = await asyncio.to_thread(lambda: next(iter(self._model.query_embed(text))).tolist())
        self._cache[key] = vec
        if len(self._cache) > 256:
            self._cache.popitem(last=False)
        return vec


class QdrantVectorStore:
    def __init__(self, embedder: Any, collection: str = "voice_agent_kb", dim: int = 768,
                 url: str = "", api_key: str = "", path: Optional[str] = None, client: Any = None):
        from qdrant_client import AsyncQdrantClient
        self.embedder = embedder
        self.dim = dim
        self.collection = f"{collection}_{dim}"
        if client is not None:
            self.client = client
        elif url:
            self.client = AsyncQdrantClient(url=url, api_key=api_key or None, timeout=5)
        else:
            self.client = AsyncQdrantClient(path=path or "./qdrant_data")
        self._ready = False

    @staticmethod
    def _point_id(chunk_id: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))

    async def ensure_collection(self) -> None:
        if self._ready:
            return
        from qdrant_client.http import models as qm
        existing = {c.name for c in (await self.client.get_collections()).collections}
        if self.collection not in existing:
            await self.client.create_collection(
                self.collection, vectors_config=qm.VectorParams(size=self.dim, distance=qm.Distance.COSINE))
        for field in ("doc_id", "filename", "node_id"):
            try:
                await self.client.create_payload_index(self.collection, field, qm.PayloadSchemaType.KEYWORD)
            except Exception:
                pass  # local mode / already exists
        self._ready = True

    async def upsert_chunks(self, chunks: List[Dict[str, Any]]) -> None:
        if not chunks:
            return
        from qdrant_client.http import models as qm
        await self.ensure_collection()
        vectors = await self.embedder.embed_documents([f"{c['title']} - {c['section']}\n{c['text']}" for c in chunks])
        points = [qm.PointStruct(
            id=self._point_id(c["chunk_id"]), vector=v,
            payload={"chunk_id": c["chunk_id"], "doc_id": c["doc_id"], "filename": c["filename"],
                     "node_id": c.get("node_id", "global")})
            for c, v in zip(chunks, vectors)]
        await self.client.upsert(self.collection, points=points, wait=True)

    async def delete_doc(self, doc_id: str) -> None:
        from qdrant_client.http import models as qm
        await self.ensure_collection()
        await self.client.delete(
            self.collection,
            points_selector=qm.FilterSelector(filter=qm.Filter(must=[
                qm.FieldCondition(key="doc_id", match=qm.MatchValue(value=doc_id))])),
            wait=True)

    async def health(self) -> Dict[str, Any]:
        """Cheap liveness probe: can we list collections, and how many points are in ours?"""
        started = time.perf_counter()
        try:
            names = {c.name for c in (await self.client.get_collections()).collections}
            points = (await self.client.count(self.collection)).count if self.collection in names else 0
            return {"ok": True, "collection": self.collection, "points": points, "dim": self.dim,
                    "ms": round((time.perf_counter() - started) * 1000, 1)}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}", "ms": round((time.perf_counter() - started) * 1000, 1)}

    async def indexed_doc_ids(self) -> set:
        await self.ensure_collection()
        ids, offset = set(), None
        while True:
            points, offset = await self.client.scroll(
                self.collection, limit=512, offset=offset, with_payload=["doc_id"], with_vectors=False)
            ids.update(p.payload.get("doc_id") for p in points if p.payload)
            if offset is None:
                return ids

    async def search(self, query: str, limit: int, node_id: Optional[str], node_docs: Optional[List[str]],
                     min_score: float) -> List[Tuple[str, float]]:
        from qdrant_client.http import models as qm
        await self.ensure_collection()
        vec = await self.embedder.embed_query(query)
        must, should = [], []
        if node_docs:
            should = [qm.FieldCondition(key="doc_id", match=qm.MatchAny(any=list(node_docs))),
                      qm.FieldCondition(key="filename", match=qm.MatchAny(any=list(node_docs)))]
        elif node_id:
            must.append(qm.FieldCondition(key="node_id", match=qm.MatchAny(any=[node_id, "global"])))
        flt = qm.Filter(must=must or None, should=should or None) if (must or should) else None
        resp = await self.client.query_points(
            self.collection, query=vec, limit=limit, query_filter=flt,
            score_threshold=min_score, with_payload=["chunk_id"])
        return [(p.payload["chunk_id"], float(p.score)) for p in resp.points if p.payload]


# ─── Engine ─────────────────────────────────────────────────────────────────

class LocalHybridRAGEngine(BaseRAGEngine):

    def __init__(self, kb_dir: Optional[str] = None, vector: Optional[QdrantVectorStore] = None,
                 semantic_timeout_ms: Optional[int] = None, min_similarity: Optional[float] = None,
                 semantic_only_margin: Optional[float] = None):
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.kb_dir = kb_dir or os.path.join(base_dir, "knowledge_base")
        os.makedirs(self.kb_dir, exist_ok=True)
        self.vector = vector
        self.semantic_timeout = (semantic_timeout_ms if semantic_timeout_ms is not None
                                 else int(os.getenv("RAG_SEMANTIC_TIMEOUT_MS", "400"))) / 1000.0
        default_sim = getattr(getattr(vector, "embedder", None), "default_min_similarity", 0.5)
        self.min_similarity = (min_similarity if min_similarity is not None
                               else float(os.getenv("RAG_MIN_SIMILARITY", default_sim)))
        # A passage found only by the semantic leg (no keyword support) must clear a higher bar,
        # otherwise small talk like "yes thanks" drags in unrelated documents.
        self.semantic_only_margin = (semantic_only_margin if semantic_only_margin is not None
                                     else float(os.getenv("RAG_SEMANTIC_ONLY_MARGIN", "0.10")))
        self.chunks: List[Dict[str, Any]] = []
        self._by_id: Dict[str, Dict[str, Any]] = {}
        self._registry: Dict[str, Dict[str, Any]] = self._load_registry()
        self._df: Counter = Counter()
        self._avgdl = 1.0
        self._semantic_docs: set = set()
        self._sync_task: Optional[asyncio.Task] = None
        self._last_sync_try = 0.0
        self.vector_status: Dict[str, Any] = {"enabled": vector is not None, "ready": False, "last_error": None}
        self._org_registry: Any = None
        self._load_all_documents()

    @property
    def org_stores(self) -> Any:
        """Tenant-specific vector stores (external Qdrant). Built on first use."""
        if self._org_registry is None:
            from .vector_stores import VectorStoreRegistry
            self._org_registry = VectorStoreRegistry(lambda: getattr(self.vector, "embedder", None))
        return self._org_registry

    # -- persistence ---------------------------------------------------------

    def _registry_path(self) -> str:
        return os.path.join(self.kb_dir, REGISTRY_FILE)

    def _load_registry(self) -> Dict[str, Dict[str, Any]]:
        try:
            with open(self._registry_path(), "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_registry(self) -> None:
        tmp = self._registry_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._registry, f, indent=2)
        os.replace(tmp, self._registry_path())

    def _doc_file(self, doc_id: str) -> Optional[str]:
        """Path of the stored text for a doc, or None. Stays inside kb_dir."""
        name = f"{doc_id}.md" if doc_id in self._registry else os.path.basename(doc_id)
        path = os.path.abspath(os.path.join(self.kb_dir, name))
        return path if path.startswith(os.path.abspath(self.kb_dir) + os.sep) and os.path.isfile(path) else None

    # -- indexing --------------------------------------------------------------

    def _make_chunks(self, doc_id: str, filename: str, node_id: str, content: str) -> List[Dict[str, Any]]:
        m = re.search(r"^#\s+(.+)$", content, re.M)
        title = m.group(1).strip() if m else os.path.splitext(filename)[0].replace("_", " ").title()
        chunks = []
        for i, (section, text) in enumerate(chunk_text(content)):
            toks = _tokens(text)
            chunks.append({"chunk_id": f"{doc_id}:{i}", "doc_id": doc_id, "filename": filename, "title": title,
                           "section": section, "text": text, "token_counts": Counter(toks), "length": len(toks),
                           "node_id": node_id or "global",
                           # a bare "# Title" with nothing under it: kept in the index (ids stay as they were) but never offered as an answer
                           "heading_only": not re.sub(r"^#{1,3}\s+.*$", "", text, flags=re.M).strip()})
        return chunks

    def _load_all_documents(self) -> None:
        self.chunks = []
        for fname in sorted(os.listdir(self.kb_dir)):
            if not fname.endswith((".md", ".txt")) or fname == REGISTRY_FILE:
                continue
            doc_id = fname[:-3] if fname[:-3] in self._registry and fname.endswith(".md") else fname
            meta = self._registry.get(doc_id, {})
            try:
                with open(os.path.join(self.kb_dir, fname), "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                self.chunks.extend(self._make_chunks(doc_id, meta.get("filename", fname),
                                                     meta.get("node_id", "global"), content))
            except Exception as e:
                logger.error(f"[RAG] Error indexing '{fname}': {e}")
        self._rebuild_stats()
        logger.info(f"[RAG] Indexed {len(self.chunks)} chunks from {self.kb_dir}")

    def _rebuild_stats(self) -> None:
        self._by_id = {c["chunk_id"]: c for c in self.chunks}
        self._df = Counter(t for c in self.chunks for t in c["token_counts"])
        self._avgdl = (sum(c["length"] for c in self.chunks) / len(self.chunks)) if self.chunks else 1.0

    # -- keyword leg -----------------------------------------------------------

    def _bm25(self, qtokens: List[str], c: Dict[str, Any], k1: float = 1.5, b: float = 0.75) -> float:
        n = len(self.chunks)
        score = 0.0
        head = f"{c['title']} {c['section']}".lower()
        for t in qtokens:
            f = c["token_counts"].get(t)
            if not f:
                continue
            idf = math.log(1 + (n - self._df[t] + 0.5) / (self._df[t] + 0.5))
            score += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * c["length"] / self._avgdl))
            if t in head:
                score += 1.5 * idf
        phrase = " ".join(qtokens)
        if len(qtokens) >= 2 and phrase in c["text"].lower():
            score += 4.0
        return score

    # -- tenant ownership --------------------------------------------------------

    def _doc_tenant(self, doc_id: str) -> Optional[int]:
        """The tenant that owns a document. Built-in documents and uploads from before tenants belong to STL."""
        owner = (self._registry.get(doc_id) or {}).get("tenant_id")
        if owner is not None:
            return owner
        from .tenancy import system_tenant_id_cached
        return system_tenant_id_cached()

    def _visible_to(self, doc_id: str, tenant_id: Optional[int]) -> bool:
        return tenant_id is None or self._doc_tenant(doc_id) == tenant_id

    @staticmethod
    def _in_scope(c: Dict[str, Any], node_id: Optional[str], node_docs: Optional[List[str]]) -> bool:
        if node_docs:
            return c["doc_id"] in node_docs or c["filename"] in node_docs
        if node_id:
            return c.get("node_id") in (node_id, "global", None)
        return True

    # -- search ----------------------------------------------------------------

    @staticmethod
    def needs_retrieval(query: str) -> bool:
        q = (query or "").strip()
        words = re.findall(r"[a-z']+", q.lower())
        if len(q) < 3 or len(words) < 2:
            return False
        return not (len(words) <= 6 and all(w.replace("'", "") in _SMALL_TALK_WORDS for w in words))

    async def search_ranked(self, query: str, node_id: Optional[str] = None,
                            node_docs: Optional[List[str]] = None, top_k: int = 2,
                            tenant_id: Optional[int] = None) -> Dict[str, Any]:
        t0 = time.perf_counter()
        timings: Dict[str, float] = {}
        if not self.needs_retrieval(query) or (not self.chunks and tenant_id is None):
            return {"results": [], "mode": "skipped", "timings_ms": {"total": 0.0}}

        from .tenancy import multi_tenant_enabled
        owner = tenant_id if multi_tenant_enabled() else None        # whose documents may answer this question
        qt = [t for t in _tokens(query) if t not in _STOP_WORDS] or _tokens(query)
        scoped = [c for c in self.chunks if not c.get("heading_only") and self._in_scope(c, node_id, node_docs) and self._visible_to(c["doc_id"], owner)]
        kw = sorted(((self._bm25(qt, c), c["chunk_id"]) for c in scoped), reverse=True)
        kw = [(s, cid) for s, cid in kw if s > 0.5][:10]
        timings["keyword"] = round((time.perf_counter() - t0) * 1000, 2)

        sem: List[Tuple[str, float]] = []
        mode = "keyword"
        org_sem = None
        if tenant_id is not None and self.vector is not None:
            t1 = time.perf_counter()
            org_sem = await self.org_stores.semantic_ids(tenant_id, query, 10, node_id, node_docs, self.min_similarity)
            if org_sem is not None:
                sem, mode = org_sem, "hybrid-org"
                timings["semantic"] = round((time.perf_counter() - t1) * 1000, 2)
        if org_sem is not None:
            pass                                  # the organization's own store answered the semantic leg
        elif self.vector is not None and self.vector_status.get("ready"):
            t1 = time.perf_counter()
            try:
                sem = await asyncio.wait_for(
                    self.vector.search(query, 10, node_id, node_docs, self.min_similarity), self.semantic_timeout)
                mode = "hybrid"
            except asyncio.TimeoutError:
                self.vector_status["last_error"] = "semantic search exceeded time budget"
                logger.info("[RAG] semantic leg timed out; using keyword results")
            except Exception as e:
                self.vector_status["last_error"] = str(e)[:200]
                logger.warning(f"[RAG] semantic leg failed, using keyword results: {e}")
            timings["semantic"] = round((time.perf_counter() - t1) * 1000, 2)
        elif self.vector is not None:
            self._kick_sync()

        fused: Dict[str, Dict[str, Any]] = {}
        for rank, (s, cid) in enumerate(kw):
            fused.setdefault(cid, {"rrf": 0.0, "bm25": None, "vector": None})
            fused[cid]["rrf"] += 1.0 / (60 + rank)
            fused[cid]["bm25"] = round(s, 3)
        for rank, (cid, s) in enumerate(sem):
            if cid not in self._by_id or self._by_id[cid].get("heading_only") or not self._visible_to(self._by_id[cid]["doc_id"], owner):
                continue
            if cid not in fused and s < self.min_similarity + self.semantic_only_margin:
                continue
            fused.setdefault(cid, {"rrf": 0.0, "bm25": None, "vector": None})
            fused[cid]["rrf"] += 1.0 / (60 + rank)
            fused[cid]["vector"] = round(s, 3)

        ranked = sorted(fused.items(), key=lambda kv: kv[1]["rrf"], reverse=True)[:top_k]
        results = []
        for cid, info in ranked:
            c = self._by_id[cid]
            results.append({"doc_id": c["doc_id"], "filename": c["filename"], "title": c["title"],
                            "section": c["section"], "text": c["text"], "bm25": info["bm25"],
                            "vector": info["vector"], "score": round(info["rrf"], 5)})
        if tenant_id is not None and self.vector is not None:
            t2 = time.perf_counter()
            for p in await self.org_stores.passages(tenant_id, query, top_k, self.min_similarity):
                results.append({"doc_id": "external", "filename": p["source"] or "external", "title": p["source"] or "Your knowledge base",
                                "section": "", "text": p["text"], "bm25": None, "vector": round(p["score"], 3),
                                "score": round(p["score"], 5), "external": True})
                mode = "hybrid+external" if mode != "keyword" else "external"
            timings["external"] = round((time.perf_counter() - t2) * 1000, 2)
        timings["total"] = round((time.perf_counter() - t0) * 1000, 2)
        return {"results": results, "mode": mode, "timings_ms": timings}

    async def search(self, query: str, node_id: Optional[str] = None,
                     node_docs: Optional[List[str]] = None, top_k: int = 2, tenant_id: Optional[int] = None, **kwargs) -> str:
        found = await self.search_ranked(query, node_id=node_id, node_docs=node_docs, top_k=top_k, tenant_id=tenant_id)
        return "\n\n---\n\n".join(
            f"[{r['title']}{' - ' + r['section'] if r['section'] else ''}]\n{r['text']}" for r in found["results"])

    # -- vector sync -----------------------------------------------------------

    def _kick_sync(self) -> None:
        if self._sync_task is None or self._sync_task.done():
            if time.time() - self._last_sync_try < 60:  # don't hammer a failing vector store every turn
                return
            self._last_sync_try = time.time()
            try:
                self._sync_task = asyncio.get_running_loop().create_task(self._sync_vectors())
            except RuntimeError:
                pass

    async def _sync_vectors(self, force: bool = False) -> Dict[str, int]:
        """Embed documents Qdrant doesn't have yet and drop vectors of documents that no longer exist."""
        if self.vector is None:
            return {"indexed": 0, "removed": 0}
        indexed = removed = 0
        try:
            warm = getattr(self.vector.embedder, "warmup", None)
            if warm:
                await warm()
            await self.vector.ensure_collection()
            have = set() if force else await self.vector.indexed_doc_ids()
            current = {c["doc_id"] for c in self.chunks}
            for doc_id in have - current:
                await self.vector.delete_doc(doc_id)
                removed += 1
            for doc_id in sorted(current - have):
                await self.vector.upsert_chunks([c for c in self.chunks if c["doc_id"] == doc_id])
                self._semantic_docs.add(doc_id)
                indexed += 1
            self._semantic_docs |= (current & have)
            self.vector_status.update(ready=True, last_error=None)
        except Exception as e:
            self.vector_status["last_error"] = str(e)[:200]
            logger.warning(f"[RAG] vector sync failed (keyword search still works): {e}")
        return {"indexed": indexed, "removed": removed}

    async def sync_into(self, store: Any, force: bool = False, tenant_id: Optional[int] = None) -> Dict[str, int]:
        """Same as the platform sync, for another store (a tenant's). Only that tenant's documents are copied there.
        Raises on failure so the caller can report it."""
        warm = getattr(store.embedder, "warmup", None)
        if warm:
            await warm()
        await store.ensure_collection()
        have = set() if force else await store.indexed_doc_ids()
        from .tenancy import multi_tenant_enabled
        owner = tenant_id if multi_tenant_enabled() else None
        current = {c["doc_id"] for c in self.chunks if self._visible_to(c["doc_id"], owner)}
        removed = indexed = 0
        for doc_id in have - current:
            await store.delete_doc(doc_id)
            removed += 1
        for doc_id in sorted(current - have):
            await store.upsert_chunks([c for c in self.chunks if c["doc_id"] == doc_id])
            indexed += 1
        return {"indexed": indexed, "removed": removed, "documents": len(current)}

    async def reindex_vectors(self) -> Dict[str, Any]:
        if self.vector is None:
            return {"enabled": False, "indexed": 0, "removed": 0}
        self._semantic_docs.clear()
        out = await self._sync_vectors(force=True)
        return {"enabled": True, **out, "ready": self.vector_status.get("ready", False),
                "last_error": self.vector_status.get("last_error")}

    # -- documents ---------------------------------------------------------------

    async def ingest_document(self, filepath: str, doc_id: str, filename: str,
                              node_id: Optional[str] = None, tenant_id: Optional[int] = None) -> Dict[str, Any]:
        """Extract, store, index (keyword now, semantic best-effort). Raises ValueError on bad input."""
        text = await asyncio.to_thread(extract_text, filepath, filename)
        stem = os.path.splitext(filename)[0].replace("_", " ").strip().title()
        body = text if re.match(r"^#\s+", text) else f"# {stem}\n\n{text}"
        with open(os.path.join(self.kb_dir, f"{doc_id}.md"), "w", encoding="utf-8") as f:
            f.write(body)
        self._registry[doc_id] = {"filename": filename, "node_id": node_id or "global",
                                  "size_kb": round(os.path.getsize(filepath) / 1024, 1),
                                  "uploaded_at": datetime.utcnow().isoformat(), "tenant_id": tenant_id}
        self._save_registry()
        new_chunks, semantic = await self._index(doc_id, filename, node_id, body)
        return {"status": "success", "doc_id": doc_id, "filename": filename,
                "chunks": len(new_chunks), "semantic": semantic}

    async def _index(self, doc_id: str, filename: str, node_id: Optional[str], body: str,
                     replace_vectors: bool = False) -> Tuple[List[Dict[str, Any]], bool]:
        """Chunk a document's text into the keyword index and, best effort, the vector store. Returns (chunks, semantic).
        `replace_vectors` clears the document's old vectors first: a shorter text must not leave its old tail searchable."""
        new_chunks = self._make_chunks(doc_id, filename, node_id or "global", body)
        self.chunks = [c for c in self.chunks if c["doc_id"] != doc_id] + new_chunks
        self._rebuild_stats()

        semantic = False
        if self.vector is not None:
            try:
                if replace_vectors:
                    await self.vector.delete_doc(doc_id)
                await self.vector.upsert_chunks(new_chunks)
                self._semantic_docs.add(doc_id)
                self.vector_status["last_error"] = None
                semantic = True
                if not self.vector_status.get("ready"):
                    self._kick_sync()  # older documents may still be missing from Qdrant
            except Exception as e:
                if replace_vectors:
                    self._semantic_docs.discard(doc_id)
                self.vector_status["last_error"] = str(e)[:200]
                logger.warning(f"[RAG] '{filename}' indexed for keyword search only: {e}")
        return new_chunks, semantic

    def read_document(self, doc_id: str, tenant_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """The stored text of one document, or None when this tenant has no such document. `editable` is false for the built-in ones."""
        if not self._visible_to(doc_id, tenant_id):
            return None
        if doc_id not in self._registry and not any(c["doc_id"] == doc_id for c in self.chunks):
            return None                                   # only what is indexed is readable: not the registry or any other file in the folder
        path = self._doc_file(doc_id)
        if not path:
            return None
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError:
            return None
        first = next((c for c in self.chunks if c["doc_id"] == doc_id), None)
        return {"id": doc_id, "name": (self._registry.get(doc_id) or {}).get("filename") or (first or {}).get("filename") or os.path.basename(path),
                "title": (first or {}).get("title", ""), "content": text, "editable": doc_id in self._registry}

    async def update_document_text(self, doc_id: str, text: str, tenant_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Replace the text of an uploaded document and index it again, keeping its name, owner and place. None when this tenant has no such
        uploaded document (a built-in one cannot be changed). Raises ValueError when the text is empty or too long."""
        if doc_id not in self._registry or not self._visible_to(doc_id, tenant_id):
            return None
        body = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not body:
            raise ValueError("The document cannot be empty.")
        if len(body) > MAX_EDIT_CHARS:
            raise ValueError(f"The document is too long (limit {MAX_EDIT_CHARS:,} characters).")
        meta = self._registry[doc_id]
        filename = meta.get("filename") or doc_id
        if not re.match(r"^#\s+", body):
            old = next((c["title"] for c in self.chunks if c["doc_id"] == doc_id), "")
            body = f"# {old or os.path.splitext(filename)[0].replace('_', ' ').strip().title()}\n\n{body}"
        path = os.path.join(self.kb_dir, f"{doc_id}.md")
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            f.write(body)
        os.replace(path + ".tmp", path)                       # a reader never sees half a document
        self._registry[doc_id] = {**meta, "size_kb": round(len(body.encode("utf-8")) / 1024, 1), "edited_at": datetime.utcnow().isoformat()}
        self._save_registry()
        new_chunks, semantic = await self._index(doc_id, filename, meta.get("node_id"), body, replace_vectors=True)
        return {"id": doc_id, "name": filename, "chunks": len(new_chunks), "semantic": semantic}

    def list_documents(self, tenant_id: Optional[int] = None) -> List[Dict[str, Any]]:
        docs: Dict[str, Dict[str, Any]] = {}
        for c in self.chunks:
            if not self._visible_to(c["doc_id"], tenant_id):
                continue
            d = docs.get(c["doc_id"])
            if d is None:
                meta = self._registry.get(c["doc_id"])
                path = self._doc_file(c["doc_id"])
                mtime = os.path.getmtime(path) if path else None
                docs[c["doc_id"]] = {
                    "id": c["doc_id"], "name": c["filename"], "filename": c["filename"], "title": c["title"],
                    "node_id": c.get("node_id", "global"), "chunks": 1,
                    "size_kb": (meta or {}).get("size_kb") or (round(os.path.getsize(path) / 1024, 1) if path else 0),
                    "uploaded_at": (meta or {}).get("uploaded_at") or
                                   (datetime.utcfromtimestamp(mtime).isoformat() if mtime else None),
                    "source": "upload" if meta else "builtin",
                    "semantic": c["doc_id"] in self._semantic_docs,
                }
            else:
                d["chunks"] += 1
        return sorted(docs.values(), key=lambda d: d.get("uploaded_at") or "", reverse=True)

    async def delete_document(self, doc_id: str, tenant_id: Optional[int] = None) -> bool:
        if not self._visible_to(doc_id, tenant_id):
            return False                          # another tenant's document answers "not found"
        path = self._doc_file(doc_id)
        known = any(c["doc_id"] == doc_id for c in self.chunks)
        if not path and not known and doc_id not in self._registry:
            return False
        if path:
            os.remove(path)
        if doc_id in self._registry:
            del self._registry[doc_id]
            self._save_registry()
        self.chunks = [c for c in self.chunks if c["doc_id"] != doc_id]
        self._rebuild_stats()
        self._semantic_docs.discard(doc_id)
        if self.vector is not None:
            try:
                await self.vector.delete_doc(doc_id)
            except Exception as e:
                self.vector_status["last_error"] = str(e)[:200]
                logger.warning(f"[RAG] vector delete failed for {doc_id} (will be cleaned on next sync): {e}")
        return True

    def stats(self, tenant_id: Optional[int] = None) -> Dict[str, Any]:
        mine = [c for c in self.chunks if self._visible_to(c["doc_id"], tenant_id)]
        return {"documents": len({c["doc_id"] for c in mine}), "chunks": len(mine),
                "semantic_documents": len({d for d in self._semantic_docs if self._visible_to(d, tenant_id)}),
                "semantic_timeout_ms": int(self.semantic_timeout * 1000), "vector": dict(self.vector_status),
                "embedding_model": getattr(getattr(self.vector, "embedder", None), "model", None)}


# ─── Factory (process-wide singleton so uploads are visible to every caller) ──

_ENGINE: Optional[LocalHybridRAGEngine] = None


def _build_embedder(cfg: AppConfig) -> Optional[Any]:
    """EMBEDDING_PROVIDER = auto | fastembed | gemini | none. auto prefers the local model."""
    provider = os.getenv("EMBEDDING_PROVIDER", "auto").lower().strip()
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if provider in ("auto", "fastembed"):
        import importlib.util
        if importlib.util.find_spec("fastembed") is not None:
            return FastEmbedEmbedder(cache_dir=os.getenv("FASTEMBED_CACHE_PATH") or
                                     os.path.join(base, "knowledge_base", ".models"))
        if provider == "fastembed":
            logger.warning("[RAG] EMBEDDING_PROVIDER=fastembed but fastembed is not installed")
    if provider in ("auto", "gemini") and cfg.llm.gemini_api_key:
        return GeminiEmbedder(cfg.llm.gemini_api_key, dim=int(os.getenv("EMBEDDING_DIM", cfg.vector_db.embedding_dimension)))
    return None


def _build_engine(config: Optional[AppConfig] = None) -> LocalHybridRAGEngine:
    cfg = config or get_config()
    vector = None
    embedder = _build_embedder(cfg) if cfg.vector_db.provider == "qdrant" else None
    if embedder is not None:
        try:
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            vector = QdrantVectorStore(
                embedder, collection=f"{cfg.vector_db.collection_name}_{embedder.slug}", dim=embedder.dim,
                url=cfg.vector_db.qdrant_url, api_key=cfg.vector_db.qdrant_api_key,
                path=os.path.join(base, "qdrant_data", "embedded_kb"))
            logger.info(f"[RAG] semantic search enabled ({embedder.model})")
        except Exception as e:
            logger.warning(f"[RAG] semantic search disabled (vector store unavailable): {e}")
    else:
        logger.info("[RAG] semantic search disabled (no embedding provider available; keyword search only)")
    return LocalHybridRAGEngine(vector=vector)


class RAGEngineFactory:
    @staticmethod
    def get_engine(config: Optional[AppConfig] = None) -> LocalHybridRAGEngine:
        global _ENGINE
        if _ENGINE is None:
            _ENGINE = _build_engine(config)
        return _ENGINE


def RAGEngine(config: Optional[AppConfig] = None) -> LocalHybridRAGEngine:
    return RAGEngineFactory.get_engine(config)
