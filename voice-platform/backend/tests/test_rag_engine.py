import asyncio
import os

import pytest
from qdrant_client import AsyncQdrantClient

from app.rag_engine import (
    LocalHybridRAGEngine, QdrantVectorStore, chunk_text, extract_text,
)

CONCEPTS = {
    "price": 0, "cost": 0, "fee": 0, "charges": 0, "pricing": 0, "expensive": 0,
    "warranty": 1, "guarantee": 1, "coverage": 1,
    "refund": 2, "return": 2, "money": 2,
    "printer": 3, "paper": 3, "jam": 3,
}
DIM = 8


class FakeEmbedder:
    """Concept-bucket embeddings: synonyms share a dimension, so semantic hits need no shared words."""
    model = "fake"

    def __init__(self, delay=0.0, fail=False):
        self.delay, self.fail = delay, fail

    def _vec(self, text):
        v = [0.01] * DIM
        for w in text.lower().replace("?", " ").replace(".", " ").split():
            if w in CONCEPTS:
                v[CONCEPTS[w]] += 1.0
        return v

    async def embed_documents(self, texts):
        if self.fail:
            raise RuntimeError("embedding down")
        return [self._vec(t) for t in texts]

    async def embed_query(self, text):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("embedding down")
        return self._vec(text)


def write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return str(p)


def make_engine(tmp_path, vector=True, embedder=None, timeout_ms=400):
    store = None
    if vector:
        store = QdrantVectorStore(embedder or FakeEmbedder(), dim=DIM, client=AsyncQdrantClient(location=":memory:"))
    return LocalHybridRAGEngine(kb_dir=str(tmp_path / "kb"), vector=store,
                                semantic_timeout_ms=timeout_ms, min_similarity=0.5)


# ─── extraction & chunking ───────────────────────────────────────────────────

def test_extract_text_formats(tmp_path):
    assert "hello world document" in extract_text(write(tmp_path, "a.md", "# T\n\nhello world document body"), "a.md")
    html = write(tmp_path, "a.html", "<html><script>x=1</script><h1>Title</h1><p>Return policy is thirty days.</p></html>")
    out = extract_text(html, "a.html")
    assert "Return policy is thirty days." in out and "x=1" not in out


def test_extract_docx(tmp_path):
    import docx
    d = docx.Document()
    d.add_heading("Warranty", 1)
    d.add_paragraph("The warranty lasts twelve months from purchase.")
    path = str(tmp_path / "w.docx")
    d.save(path)
    out = extract_text(path, "w.docx")
    assert "## Warranty" in out and "twelve months" in out


def test_extract_rejects_unsupported_and_empty(tmp_path):
    with pytest.raises(ValueError, match="Unsupported"):
        extract_text(write(tmp_path, "a.exe", "x" * 100), "a.exe")
    with pytest.raises(ValueError, match="No readable text"):
        extract_text(write(tmp_path, "e.txt", "  "), "e.txt")
    from pypdf import PdfWriter
    pdf = tmp_path / "blank.pdf"
    w = PdfWriter()
    w.add_blank_page(100, 100)
    with open(pdf, "wb") as f:
        w.write(f)
    with pytest.raises(ValueError, match="No readable text"):
        extract_text(str(pdf), "blank.pdf")


def test_chunk_text_sections_and_long_paragraphs():
    text = "# Doc\n\nintro paragraph here is long enough\n\n## Pricing\n\n" + ("word " * 400)
    chunks = chunk_text(text, max_chars=300, overlap=50)
    assert any(s == "Pricing" for s, _ in chunks)
    assert all(len(t) <= 300 for _, t in chunks)
    assert len([1 for s, _ in chunks if s == "Pricing"]) > 1


@pytest.mark.asyncio
async def test_a_bare_title_never_takes_one_of_the_places_among_the_answers(tmp_path):
    """Documents the Architect writes start with '# Title' and nothing under it; on a call that bare title took one of the three places for
    every question that used its words, and a rule that was wanted was left out."""
    eng = make_engine(tmp_path, vector=False)
    body = ("# Policy and renewals\n\n## Policy expiry\nA policy ends at midnight on its end date and renewals start at payment.\n\n"
            "## Cancelling a policy\nA customer can cancel within 15 days of buying for a full refund minus stamp duty.\n\n## Hours\nOpen 9 to 7.")
    await eng.ingest_document(write(tmp_path, "p.md", body), "p1", "Policy_and_renewals.md")
    found = await eng.search_ranked("policy renewals", top_k=3)
    sections = [r["section"] for r in found["results"]]
    assert sections and "Policy and renewals" not in sections and "Policy expiry" in sections
    assert "Open 9 to 7" in await eng.search("opening hours")                           # a short answer under a heading is still found
    assert [c["chunk_id"] for c in eng.chunks][:2] == ["p1:0", "p1:1"]                  # the ids did not move: vectors already stored still match
    assert eng.chunks[0]["heading_only"] and not eng.chunks[1]["heading_only"]


# ─── documents: add / list / search / delete ────────────────────────────────

@pytest.mark.asyncio
async def test_ingest_list_search_delete_keyword_only(tmp_path):
    eng = make_engine(tmp_path, vector=False)
    src = write(tmp_path, "up.md", "# Printer Guide\n\n## Paper jam\n\nOpen the rear cover and remove the jammed paper gently.")
    res = await eng.ingest_document(src, "doc1", "Printer_Guide.md")
    assert res["chunks"] >= 1 and res["semantic"] is False

    docs = eng.list_documents()
    assert [d["name"] for d in docs] == ["Printer_Guide.md"] and docs[0]["source"] == "upload"
    assert docs[0]["chunks"] == res["chunks"]

    assert "rear cover" in await eng.search("how to clear a paper jam")
    assert await eng.delete_document("doc1") is True
    assert await eng.search("how to clear a paper jam") == ""
    assert eng.list_documents() == []
    assert not os.path.exists(os.path.join(eng.kb_dir, "doc1.md"))
    assert await eng.delete_document("doc1") is False


@pytest.mark.asyncio
async def test_registry_persists_across_restart(tmp_path):
    eng = make_engine(tmp_path, vector=False)
    await eng.ingest_document(write(tmp_path, "u.md", "# Fees\n\nThe consultation fee is 800 rupees for everyone."), "id-1", "Fees.md")
    again = make_engine(tmp_path, vector=False)
    assert [d["name"] for d in again.list_documents()] == ["Fees.md"]
    assert "800 rupees" in await again.search("what is the consultation fee")


@pytest.mark.asyncio
async def test_scoping_by_name_or_id_and_node(tmp_path):
    eng = make_engine(tmp_path, vector=False)
    await eng.ingest_document(write(tmp_path, "a.md", "# A\n\nThe alpha product warranty is two years long."), "ida", "alpha.md", node_id="n1")
    await eng.ingest_document(write(tmp_path, "b.md", "# B\n\nThe beta product warranty is five years long."), "idb", "beta.md", node_id="n2")
    q = "what is the product warranty"
    assert "alpha" in await eng.search(q, node_docs=["alpha.md"])
    assert "beta" in await eng.search(q, node_docs=["idb"])
    assert "beta" not in await eng.search(q, node_id="n1")


@pytest.mark.asyncio
async def test_delete_blocks_path_traversal(tmp_path):
    eng = make_engine(tmp_path, vector=False)
    outside = tmp_path / "secret.md"
    outside.write_text("# secret\n\nkeep me safe please", encoding="utf-8")
    assert await eng.delete_document("../secret.md") is False
    assert outside.exists()


@pytest.mark.asyncio
async def test_builtin_files_are_listed(tmp_path):
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "legacy_faq.md").write_text("# Legacy FAQ\n\n## Hours\n\nWe are open from nine to five on weekdays.", encoding="utf-8")
    eng = LocalHybridRAGEngine(kb_dir=str(kb))
    d = eng.list_documents()[0]
    assert d["id"] == "legacy_faq.md" and d["source"] == "builtin"
    assert await eng.delete_document("legacy_faq.md") is True


def test_retrieval_gate():
    g = LocalHybridRAGEngine.needs_retrieval
    assert not g("yes") and not g("okay.") and not g("hi") and not g("")
    assert not g("yes thanks") and not g("okay that works for me") and not g("hello can you hear me")
    assert not g("that's all, thank you")
    assert g("AMC pricing") and g("what is the warranty period") and g("what are your clinic timings")
    assert g("my printer keeps jamming paper")


# ─── semantic leg ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_semantic_finds_what_keywords_cannot(tmp_path):
    eng = make_engine(tmp_path)
    await eng.ingest_document(write(tmp_path, "p.md", "# Plans\n\n## Pricing\n\nThe annual pricing is 5000 rupees per device."), "p1", "plans.md")
    await eng.ingest_document(write(tmp_path, "w.md", "# Warranty\n\n## Coverage\n\nThe warranty coverage lasts two years."), "w1", "warranty.md")
    eng.vector_status["ready"] = True

    query = "how expensive is it"          # shares no keyword with the pricing doc
    kw_only = make_engine(tmp_path / "x", vector=False)
    assert await kw_only.search(query) == ""

    out = await eng.search_ranked(query, top_k=1)
    assert out["mode"] == "hybrid"
    assert out["results"][0]["doc_id"] == "p1" and out["results"][0]["vector"] is not None


@pytest.mark.asyncio
async def test_delete_removes_vectors(tmp_path):
    eng = make_engine(tmp_path)
    await eng.ingest_document(write(tmp_path, "p.md", "# Plans\n\n## Pricing\n\nThe annual pricing is 5000 rupees per device."), "p1", "plans.md")
    eng.vector_status["ready"] = True
    assert (await eng.search_ranked("how expensive is it"))["results"]
    await eng.delete_document("p1")
    assert (await eng.search_ranked("how expensive is it"))["results"] == []
    assert "p1" not in await eng.vector.indexed_doc_ids()


@pytest.mark.asyncio
async def test_slow_semantic_leg_falls_back_to_keyword(tmp_path):
    eng = make_engine(tmp_path, embedder=FakeEmbedder(delay=0.5), timeout_ms=50)
    await eng.ingest_document(write(tmp_path, "w.md", "# Warranty\n\n## Coverage\n\nThe warranty coverage lasts two years."), "w1", "w.md")
    eng.vector_status["ready"] = True
    out = await eng.search_ranked("warranty coverage period")
    assert out["mode"] == "keyword" and out["results"][0]["doc_id"] == "w1"
    assert out["timings_ms"]["total"] < 400


@pytest.mark.asyncio
async def test_embedding_failure_keeps_keyword_search_working(tmp_path):
    eng = make_engine(tmp_path, embedder=FakeEmbedder(fail=True))
    res = await eng.ingest_document(write(tmp_path, "w.md", "# Warranty\n\n## Coverage\n\nThe warranty coverage lasts two years."), "w1", "w.md")
    assert res["status"] == "success" and res["semantic"] is False
    assert eng.vector_status["last_error"]
    eng.vector_status["ready"] = True
    out = await eng.search_ranked("warranty coverage period")
    assert out["mode"] == "keyword" and out["results"]


@pytest.mark.asyncio
async def test_sync_indexes_missing_docs_and_drops_stale(tmp_path):
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "plans.md").write_text("# Plans\n\n## Pricing\n\nThe annual pricing is 5000 rupees per device.", encoding="utf-8")
    store = QdrantVectorStore(FakeEmbedder(), dim=DIM, client=AsyncQdrantClient(location=":memory:"))
    eng = LocalHybridRAGEngine(kb_dir=str(kb), vector=store, semantic_timeout_ms=400, min_similarity=0.5)
    await store.ensure_collection()
    stale = [{"chunk_id": "gone:0", "doc_id": "gone", "filename": "gone.md", "title": "t", "section": "s",
              "text": "old text about refund money", "node_id": "global"}]
    await store.upsert_chunks(stale)

    report = await eng._sync_vectors()
    assert report == {"indexed": 1, "removed": 1}
    assert eng.vector_status["ready"] is True
    assert await store.indexed_doc_ids() == {"plans.md"}
    assert (await eng.search_ranked("how expensive is it"))["mode"] == "hybrid"


class FixedSimEmbedder:
    """Query vectors sit at a chosen cosine similarity to every document vector."""
    model = "fixed"

    def __init__(self, sim):
        self.sim = sim

    async def embed_documents(self, texts):
        return [[1.0] + [0.0] * (DIM - 1) for _ in texts]

    async def embed_query(self, text):
        return [self.sim, (1 - self.sim ** 2) ** 0.5] + [0.0] * (DIM - 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("sim,expect_hit", [(0.55, False), (0.65, True)])
async def test_semantic_only_hits_need_a_higher_bar(tmp_path, sim, expect_hit):
    store = QdrantVectorStore(FixedSimEmbedder(sim), dim=DIM, client=AsyncQdrantClient(location=":memory:"))
    eng = LocalHybridRAGEngine(kb_dir=str(tmp_path / "kb"), vector=store, semantic_timeout_ms=400, min_similarity=0.5)
    await eng.ingest_document(write(tmp_path, "w.md", "# Clinic\n\n## Hours\n\nThe clinic opens at nine in the morning."), "c1", "clinic.md")
    eng.vector_status["ready"] = True
    out = await eng.search_ranked("completely unrelated phrasing zebra")   # no keyword overlap
    assert bool(out["results"]) is expect_hit


@pytest.mark.asyncio
async def test_keyword_supported_hit_survives_lower_similarity(tmp_path):
    store = QdrantVectorStore(FixedSimEmbedder(0.55), dim=DIM, client=AsyncQdrantClient(location=":memory:"))
    eng = LocalHybridRAGEngine(kb_dir=str(tmp_path / "kb"), vector=store, semantic_timeout_ms=400, min_similarity=0.5)
    await eng.ingest_document(write(tmp_path, "w.md", "# Clinic\n\n## Hours\n\nThe clinic opens at nine in the morning."), "c1", "clinic.md")
    eng.vector_status["ready"] = True
    out = await eng.search_ranked("when does the clinic open")
    assert out["results"] and out["results"][0]["bm25"] is not None and out["results"][0]["vector"] == pytest.approx(0.55, abs=0.01)


@pytest.mark.asyncio
async def test_failed_sync_is_not_retried_every_turn(tmp_path):
    eng = make_engine(tmp_path, embedder=FakeEmbedder(fail=True))
    await eng.ingest_document(write(tmp_path, "w.md", "# Warranty\n\n## Coverage\n\nThe warranty coverage lasts two years."), "w1", "w.md")
    calls = {"n": 0}
    real = eng._sync_vectors

    async def counting(force=False):
        calls["n"] += 1
        return await real(force)

    eng._sync_vectors = counting
    for _ in range(5):
        await eng.search_ranked("warranty coverage period")
        await asyncio.sleep(0.05)
    assert calls["n"] <= 1
