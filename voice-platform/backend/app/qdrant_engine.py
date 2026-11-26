"""
Qdrant Cloud Vector Database Engine
Provides scalable, cloud-native vector search and metadata-filtered retrieval for RAG.
Implements BaseRAGEngine.
"""

import os
import uuid
import logging
from typing import List, Dict, Any, Optional
import google.generativeai as genai

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qmodels

from .interfaces import BaseRAGEngine
from .config import VectorDBSettings, get_config

logger = logging.getLogger(__name__)

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None


class QdrantRAGEngine(BaseRAGEngine):
    """
    Production-grade Vector Knowledge Base powered by Qdrant.
    Supports Qdrant Cloud, Self-Hosted Qdrant Docker, or Local Embedded storage.
    """

    def __init__(self, settings: Optional[VectorDBSettings] = None):
        self.settings = settings or get_config().vector_db
        self.collection_name = self.settings.collection_name
        self.dimension = self.settings.embedding_dimension
        self._client: Optional[AsyncQdrantClient] = None
        self._initialized = False

    async def _get_client(self) -> AsyncQdrantClient:
        if self._client is None:
            if self.settings.qdrant_url:
                logger.info(f"[Qdrant] Connecting to Qdrant cluster at {self.settings.qdrant_url}")
                self._client = AsyncQdrantClient(
                    url=self.settings.qdrant_url,
                    api_key=self.settings.qdrant_api_key or None,
                    timeout=10.0
                )
            else:
                # Embedded local/in-memory mode for standalone or test execution
                storage_path = os.getenv("QDRANT_LOCAL_PATH", "./qdrant_data")
                os.makedirs(storage_path, exist_ok=True)
                logger.info(f"[Qdrant] Running in embedded local mode at {storage_path}")
                self._client = AsyncQdrantClient(path=storage_path)

        if not self._initialized:
            await self._ensure_collection()
            self._initialized = True

        return self._client

    async def _ensure_collection(self):
        """Create the target vector collection if it does not already exist."""
        try:
            collections = await self._client.get_collections()
            existing_names = [c.name for c in collections.collections]
            if self.collection_name not in existing_names:
                logger.info(f"[Qdrant] Creating collection '{self.collection_name}' (dim={self.dimension}, metric=COSINE)")
                await self._client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=qmodels.VectorParams(
                        size=self.dimension,
                        distance=qmodels.Distance.COSINE
                    )
                )
                # Create payload index on node_id for fast scoped filtering
                await self._client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name="node_id",
                    field_schema=qmodels.PayloadSchemaType.KEYWORD
                )
                await self._client.create_payload_index(
                    collection_name=self.collection_name,
                    field_name="doc_id",
                    field_schema=qmodels.PayloadSchemaType.KEYWORD
                )
        except Exception as e:
            logger.warning(f"[Qdrant] Collection initialization notice: {e}")

    async def embed_text(self, text: str) -> List[float]:
        """Generate embedding vector using Gemini or fallback zero vector."""
        try:
            result = await genai.embed_content_async(
                model="models/gemini-embedding-001",
                content=text,
                task_type="retrieval_document"
            )
            return result['embedding']
        except Exception as e:
            logger.debug(f"[Qdrant] Embedding generation notice: {e}")
            return [0.0] * self.dimension

    def _extract_text(self, filepath: str) -> str:
        """Extract text from PDF, TXT, or Markdown documents."""
        ext = os.path.splitext(filepath)[1].lower()
        if ext == ".pdf":
            if not PdfReader:
                raise ImportError("pypdf is required for PDF parsing.")
            reader = PdfReader(filepath)
            text = ""
            for page in reader.pages:
                extracted = page.extract_text()
                if extracted:
                    text += extracted + "\n"
            return text
        else:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read()

    def _chunk_text(self, text: str, chunk_size: int = 500, overlap: int = 100) -> List[str]:
        """Split document text into overlapping chunks."""
        chunks = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            chunks.append(text[start:end].strip())
            start += chunk_size - overlap
        return [c for c in chunks if len(c) > 10]

    async def ingest_document(
        self,
        filepath: str,
        doc_id: str,
        filename: str,
        node_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Parse, chunk, embed, and index a document into Qdrant."""
        try:
            text = self._extract_text(filepath)
            if not text:
                return {"error": "Could not extract text from file"}

            chunks = self._chunk_text(text)
            if not chunks:
                return {"error": "No text content found to index"}

            client = await self._get_client()
            points = []

            for i, chunk in enumerate(chunks):
                vector = await self.embed_text(chunk)
                point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{doc_id}_{i}"))
                payload = {
                    "doc_id": doc_id,
                    "filename": filename,
                    "chunk_index": i,
                    "text": chunk,
                    "node_id": node_id or "global"
                }
                points.append(qmodels.PointStruct(
                    id=point_id,
                    vector=vector,
                    payload=payload
                ))

            # Batch upsert to Qdrant
            await client.upsert(
                collection_name=self.collection_name,
                points=points
            )

            logger.info(f"[Qdrant] Ingested '{filename}' with {len(points)} chunks into collection '{self.collection_name}'")
            return {
                "status": "success",
                "doc_id": doc_id,
                "filename": filename,
                "chunks": len(points)
            }
        except Exception as e:
            logger.error(f"[Qdrant] Document ingestion error: {e}", exc_info=True)
            return {"error": str(e)}

    async def search(
        self,
        query: str,
        node_id: Optional[str] = None,
        top_k: int = 3,
        **kwargs
    ) -> str:
        """Search relevant document chunks scoped to a workflow node."""
        try:
            client = await self._get_client()
            query_vector = await self.embed_text(query)

            # Build scoped node filter if specified
            query_filter = None
            if node_id:
                query_filter = qmodels.Filter(
                    should=[
                        qmodels.FieldCondition(key="node_id", match=qmodels.MatchValue(value=node_id)),
                        qmodels.FieldCondition(key="node_id", match=qmodels.MatchValue(value="global"))
                    ]
                )

            search_results = await client.search(
                collection_name=self.collection_name,
                query_vector=query_vector,
                query_filter=query_filter,
                limit=top_k
            )

            # If scoped node search returns 0 results, fall back to searching all documents
            if not search_results and query_filter is not None:
                search_results = await client.search(
                    collection_name=self.collection_name,
                    query_vector=query_vector,
                    query_filter=None,
                    limit=top_k
                )

            results = [hit.payload.get("text", "") for hit in search_results if hit.payload and hit.payload.get("text")]
            if results and any(len(r.strip()) > 0 for r in results):
                return "\n\n".join(results)

        except Exception as e:
            logger.debug(f"[Qdrant] Vector search notice: {e}")

        # Graceful lexical fallback across Qdrant stored points
        try:
            client = await self._get_client()
            records, _ = await client.scroll(
                collection_name=self.collection_name,
                limit=200,
                with_payload=True,
                with_vectors=False
            )
            query_words = set(query.lower().split())
            scored = []
            for rec in records:
                if not rec.payload:
                    continue
                point_node = rec.payload.get("node_id")
                if node_id and point_node not in [node_id, "global", None]:
                    continue
                text = rec.payload.get("text", "")
                text_words = set(text.lower().split())
                score = len(query_words.intersection(text_words))
                if score > 0:
                    scored.append((score, text))
                elif not scored and len(text) > 0:
                    scored.append((0, text))
            scored.sort(key=lambda x: x[0], reverse=True)
            top_texts = [t[1] for t in scored[:top_k]]
            if top_texts:
                return "\n\n".join(top_texts)
        except Exception as e:
            logger.debug(f"[Qdrant] Lexical fallback notice: {e}")

        return ""

    async def list_documents(self) -> List[Dict[str, Any]]:
        """List all indexed documents and chunk statistics."""
        try:
            client = await self._get_client()
            records, _ = await client.scroll(
                collection_name=self.collection_name,
                limit=1000,
                with_payload=True,
                with_vectors=False
            )
            docs = {}
            for rec in records:
                if rec.payload:
                    doc_id = rec.payload.get("doc_id")
                    if doc_id:
                        if doc_id not in docs:
                            docs[doc_id] = {
                                "id": doc_id,
                                "filename": rec.payload.get("filename", "Unknown"),
                                "node_id": rec.payload.get("node_id", "global"),
                                "chunks": 1
                            }
                        else:
                            docs[doc_id]["chunks"] += 1
            return list(docs.values())
        except Exception as e:
            logger.error(f"[Qdrant] Error listing documents: {e}")
            return []

    async def delete_document(self, doc_id: str) -> bool:
        """Delete all points associated with a specific doc_id."""
        try:
            client = await self._get_client()
            await client.delete(
                collection_name=self.collection_name,
                points_selector=qmodels.FilterSelector(
                    filter=qmodels.Filter(
                        must=[qmodels.FieldCondition(key="doc_id", match=qmodels.MatchValue(value=doc_id))]
                    )
                )
            )
            logger.info(f"[Qdrant] Deleted document doc_id={doc_id}")
            return True
        except Exception as e:
            logger.error(f"[Qdrant] Error deleting document: {e}")
            return False
