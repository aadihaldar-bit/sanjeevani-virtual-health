"""External vector stores, run against a real in-memory Qdrant so the query, count and filter calls are exercised for real."""
import asyncio

import pytest
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qm

from org_env import env  # noqa: F401
from test_rag_engine import DIM, FakeEmbedder, write
from app import vector_stores as vs
from app.rag_engine import LocalHybridRAGEngine, QdrantVectorStore

pytestmark = pytest.mark.asyncio
URL = "https://93.184.216.34:6333"
KEY = "qdrant-api-key-SECRET-4321"


class SizedEmbedder(FakeEmbedder):
    dim = DIM


class Shared:
    """One in-memory Qdrant shared by every 'connection'; closing a connection must not destroy it."""

    def __init__(self):
        self.real = AsyncQdrantClient(location=":memory:")
        self.created = 0

    def factory(self, url, api_key):
        self.created += 1
        outer = self

        class Conn:
            def __getattr__(self, name):
                return getattr(outer.real, name)

            async def close(self):
                return None
        return Conn()


@pytest.fixture
def qdrant():
    return Shared()


async def seed(client, collection="their_kb", dim=DIM):
    await client.create_collection(collection, vectors_config=qm.VectorParams(size=dim, distance=qm.Distance.COSINE))
    if dim != DIM:
        return                                                    # an empty collection of the wrong size is all the test needs
    emb = FakeEmbedder()
    texts = {"p1": ("The warranty covers parts for twelve months", "warranty.pdf"),
             "p2": ("Refund money is returned in seven days", "refunds.pdf"),
             "p3": ("Printer paper jam: open the rear cover", "printer.pdf")}
    vectors = await emb.embed_documents([t for t, _ in texts.values()])
    await client.upsert(collection, wait=True, points=[
        qm.PointStruct(id=i + 1, vector=v, payload={"body": text, "doc": src, "secret_internal": "do-not-return"})
        for i, ((text, src), v) in enumerate(zip(texts.values(), vectors))])


# --- connection check ----------------------------------------------------------------------------

async def test_check_passes_when_the_collection_exists_with_the_right_size(qdrant):
    await seed(qdrant.real)
    result = await vs.check_connection(URL, KEY, "their_kb", "read_only", DIM, client_factory=qdrant.factory)
    assert result["ok"] and result["vector_size"] == DIM and result["collection_exists"] and result["ms"] >= 0


async def test_check_refuses_a_collection_with_the_wrong_vector_size(qdrant):
    await seed(qdrant.real, dim=16)
    result = await vs.check_connection(URL, KEY, "their_kb", "read_only", DIM, client_factory=qdrant.factory)
    assert not result["ok"] and "16" in result["error"] and str(DIM) in result["error"]


async def test_read_only_never_creates_a_missing_collection(qdrant):
    result = await vs.check_connection(URL, KEY, "missing", "read_only", DIM, client_factory=qdrant.factory)
    assert not result["ok"] and "does not exist" in result["error"] and not result["will_create"]
    assert "missing" not in {c.name for c in (await qdrant.real.get_collections()).collections}


async def test_index_mode_reports_that_it_will_create_the_collection(qdrant):
    result = await vs.check_connection(URL, KEY, "ours", "index", DIM, client_factory=qdrant.factory)
    assert result["ok"] and result["will_create"] and result["collection_name"] == f"ours_{DIM}"


async def test_check_refuses_internal_addresses_without_connecting(qdrant):
    result = await vs.check_connection("https://10.0.0.5:6333", KEY, "c", "index", DIM, client_factory=qdrant.factory)
    assert not result["ok"] and "private" in result["error"] and qdrant.created == 0


async def test_a_connection_error_is_reported_and_the_key_is_masked(qdrant):
    def failing(url, key):
        raise ConnectionError(f"could not connect using {key}")
    result = await vs.check_connection(URL, KEY, "c", "index", DIM, client_factory=failing)
    assert not result["ok"] and KEY not in result["error"] and "ConnectionError" in result["error"]


# --- circuit breaker ----------------------------------------------------------------------------------

def test_breaker_opens_after_repeated_failures_and_recovers_after_the_cooldown():
    clock = {"t": 0.0}
    breaker = vs.CircuitBreaker(threshold=3, cooldown=30, clock=lambda: clock["t"])
    for _ in range(2):
        breaker.record(False)
    assert breaker.allow()
    breaker.record(False)
    assert not breaker.allow()
    clock["t"] = 31
    assert breaker.allow()                 # one probe is let through
    breaker.record(False)
    assert not breaker.allow()             # a failed probe reopens it immediately
    clock["t"] = 70
    assert breaker.allow()
    breaker.record(True)
    assert breaker.allow() and breaker.failures == 0


# --- API --------------------------------------------------------------------------------------------------

def body(**over):
    base = {"url": URL, "api_key": KEY, "collection": "their_kb", "mode": "read_only", "text_field": "body", "source_field": "doc"}
    base.update(over)
    return base


async def make_org(env):
    return await env.make_org("Acme", admin_email="boss@acme.test", member_email="sam@acme.test")


async def test_saving_a_store_never_returns_or_logs_the_key(env):
    await make_org(env)
    saved = await env.put("/api/vector-stores", user="boss@acme.test", json=body())
    assert saved.status_code == 200 and KEY not in saved.text and saved.json()["has_api_key"]
    assert KEY not in (await env.get("/api/vector-stores", user="boss@acme.test")).text
    assert KEY not in (await env.get("/api/admin/audit", user="boss@acme.test")).text
    config = (await env.get("/api/vector-stores", user="boss@acme.test")).json()
    assert config["config"]["collection"] == "their_kb" and config["encryption_configured"] is True


@pytest.mark.parametrize("override,fragment", [
    ({"url": "https://10.0.0.5:6333"}, "private"),
    ({"url": "http://93.184.216.34:6333"}, "https"),
    ({"collection": "bad name!"}, "Collection"),
    ({"collection": ""}, "Collection"),
    ({"mode": "write_everything"}, "Mode"),
    ({"provider": "pinecone"}, "Supported"),
    ({"text_field": None}, "payload field"),
])
async def test_invalid_store_settings_are_refused(env, override, fragment):
    await make_org(env)
    response = await env.put("/api/vector-stores", user="boss@acme.test", json=body(**override))
    assert response.status_code in (400, 422), response.text
    if response.status_code == 400:
        assert fragment.lower() in response.json()["detail"].lower()


async def test_only_organization_admins_can_configure_and_each_org_sees_its_own(env):
    await make_org(env)
    await env.make_org("Globex", admin_email="boss@globex.test")
    assert (await env.put("/api/vector-stores", user="sam@acme.test", json=body())).status_code == 403
    await env.put("/api/vector-stores", user="boss@acme.test", json=body())
    assert (await env.get("/api/vector-stores", user="boss@globex.test")).json()["config"] is None
    assert (await env.delete("/api/vector-stores", user="boss@globex.test")).status_code == 404
    assert (await env.delete("/api/vector-stores", user="boss@acme.test")).status_code == 200
    assert (await env.get("/api/vector-stores", user="boss@acme.test")).json()["config"] is None


async def test_the_store_connection_test_uses_the_embedding_size_and_records_the_result(env, qdrant, monkeypatch):
    await make_org(env)
    await seed(qdrant.real)
    monkeypatch.setattr(vs, "make_client", qdrant.factory)
    import app.vector_stores_api as api
    monkeypatch.setattr(api, "_dim", lambda: DIM)
    await env.put("/api/vector-stores", user="boss@acme.test", json=body())
    result = (await env.post("/api/vector-stores/test", user="boss@acme.test")).json()
    assert result["ok"] and result["vector_size"] == DIM
    config = (await env.get("/api/vector-stores", user="boss@acme.test")).json()["config"]
    assert config["last_test_ok"] is True and config["dim"] == DIM
    monkeypatch.setattr(api, "_dim", lambda: 32)                       # embedding model changed: the old index no longer fits
    assert (await env.post("/api/vector-stores/test", user="boss@acme.test")).json()["ok"] is False


async def test_read_only_stores_cannot_be_reindexed(env):
    await make_org(env)
    await env.put("/api/vector-stores", user="boss@acme.test", json=body())
    assert (await env.post("/api/vector-stores/reindex", user="boss@acme.test")).status_code == 409


# --- registry: what retrieval actually gets -----------------------------------------------------------------

def registry_for(env, qdrant, **kw):
    return vs.VectorStoreRegistry(lambda: SizedEmbedder(), ttl=0.0, client_factory=qdrant.factory, session_factory=env.maker, **kw)


async def configure(env, **over):
    await env.put("/api/vector-stores", user="boss@acme.test", json=body(**over))


async def test_read_only_store_returns_passages_with_the_configured_fields_only(env, qdrant):
    await make_org(env)
    await seed(qdrant.real)
    await configure(env)
    found = await registry_for(env, qdrant).passages(1, "what does the warranty cover", 2, 0.5)
    assert found and found[0]["text"].startswith("The warranty") and found[0]["source"] == "warranty.pdf"
    assert all("do-not-return" not in str(p) for p in found)               # unmapped payload fields never leave the store


async def test_read_only_store_is_never_written_to(env, qdrant):
    await make_org(env)
    await seed(qdrant.real)
    await configure(env)
    before = (await qdrant.real.count("their_kb")).count
    registry = registry_for(env, qdrant)
    await registry.passages(1, "printer paper jam", 2, 0.5)
    assert (await qdrant.real.count("their_kb")).count == before
    assert {c.name for c in (await qdrant.real.get_collections()).collections} == {"their_kb"}


async def test_no_store_or_a_disabled_store_means_no_passages_and_no_connection(env, qdrant):
    await make_org(env)
    registry = registry_for(env, qdrant)
    assert await registry.passages(1, "anything at all", 2, 0.5) == [] and await registry.passages(None, "x y", 2, 0.5) == []
    await seed(qdrant.real)
    await configure(env, enabled=False)
    assert await registry_for(env, qdrant).passages(1, "warranty coverage", 2, 0.5) == [] and qdrant.created == 0


async def test_a_slow_store_cannot_hold_up_the_call_and_repeated_failures_open_the_breaker(env, qdrant, monkeypatch):
    await make_org(env)
    await seed(qdrant.real)
    await configure(env)
    registry = registry_for(env, qdrant)
    registry.external_timeout = 0.05

    calls = {"n": 0}

    class SlowEmbedder(SizedEmbedder):
        async def embed_query(self, text):
            calls["n"] += 1
            await asyncio.sleep(1)
            return await super().embed_query(text)

    registry._embedder = lambda: SlowEmbedder()
    import time
    started = time.perf_counter()
    for _ in range(3):
        assert await registry.passages(1, "warranty coverage", 2, 0.5) == []
    assert time.perf_counter() - started < 1.0                      # three timeouts of 50 ms, not three seconds
    assert calls["n"] == 3
    assert await registry.passages(1, "warranty coverage", 2, 0.5) == []
    assert calls["n"] == 3                                         # breaker open: the store is not even asked


async def test_an_unsafe_stored_url_is_never_connected_to(env, qdrant):
    await make_org(env)
    await configure(env)
    from sqlalchemy import update
    from app.models import VectorStoreConfig
    async with env.maker() as db:                                   # simulate DNS or data changing after it was saved
        await db.execute(update(VectorStoreConfig).values(url="https://10.0.0.9:6333"))
        await db.commit()
    assert await registry_for(env, qdrant).passages(1, "warranty coverage", 2, 0.5) == [] and qdrant.created == 0


# --- index mode and the retrieval engine -------------------------------------------------------------------------

def engine_with_docs(tmp_path):
    kb = tmp_path / "kb"
    kb.mkdir()
    engine = LocalHybridRAGEngine(kb_dir=str(kb), vector=QdrantVectorStore(SizedEmbedder(), dim=DIM, client=AsyncQdrantClient(location=":memory:")),
                                  semantic_timeout_ms=400, min_similarity=0.5)
    return engine


async def ingest(engine, tmp_path, name, text):
    return await engine.ingest_document(write(tmp_path, name, text), doc_id=name.split(".")[0], filename=name)


async def test_index_mode_copies_our_documents_into_the_organizations_store_and_searches_there(env, qdrant, tmp_path):
    await make_org(env)
    engine = engine_with_docs(tmp_path)
    await ingest(engine, tmp_path, "warranty.md", "# Warranty\n\nThe warranty covers parts and labour for twelve months.")
    await ingest(engine, tmp_path, "refunds.md", "# Refunds\n\nRefund money is returned within seven days of the return.")
    await configure(env, mode="index", text_field=None, source_field=None, collection="ours")
    engine._org_registry = registry_for(env, qdrant)
    resolved = await engine.org_stores.for_org(1)
    assert resolved.mode == "index" and not resolved.ready                   # empty until indexed: retrieval falls back
    counts = await engine.sync_into(resolved.index_store)
    assert counts == {"indexed": 2, "removed": 0, "documents": 2}
    again = await engine.sync_into(resolved.index_store)
    assert again["indexed"] == 0                                              # a second sync copies nothing new
    assert (await qdrant.real.count(f"ours_{DIM}")).count >= 2
    ids = await engine.org_stores.semantic_ids(1, "how long is the guarantee coverage", 5, None, None, 0.5)
    assert ids and any("warranty" in cid for cid, _ in ids)


async def test_engine_uses_the_organizations_store_for_the_semantic_leg_and_falls_back_when_it_fails(env, qdrant, tmp_path):
    await make_org(env)
    engine = engine_with_docs(tmp_path)
    await ingest(engine, tmp_path, "warranty.md", "# Warranty\n\nThe warranty covers parts and labour for twelve months.")
    engine.vector_status["ready"] = True
    await configure(env, mode="index", text_field=None, source_field=None, collection="ours")
    engine._org_registry = registry_for(env, qdrant)
    await engine.sync_into((await engine.org_stores.for_org(1)).index_store)
    hit = await engine.search_ranked("is the guarantee covering parts", tenant_id=1)
    assert hit["mode"] == "hybrid-org" and hit["results"] and hit["results"][0]["doc_id"] == "warranty"
    class SlowEmbedder(SizedEmbedder):                                         # now the organization's store is too slow
        async def embed_query(self, text):
            await asyncio.sleep(0.5)
            return await super().embed_query(text)

    engine.org_stores.external_timeout = 0.05
    engine.org_stores._embedder = lambda: SlowEmbedder()
    engine.org_stores.invalidate()
    fallback = await engine.search_ranked("is the guarantee covering parts", tenant_id=1)
    assert fallback["mode"] in ("hybrid", "keyword") and fallback["results"]  # the platform store still answers


async def test_read_only_passages_are_added_to_local_results_and_work_without_any_local_documents(env, qdrant, tmp_path):
    await make_org(env)
    await seed(qdrant.real)
    await configure(env)
    engine = engine_with_docs(tmp_path)                                       # no local documents at all
    engine._org_registry = registry_for(env, qdrant)
    found = await engine.search_ranked("what does the warranty cover", tenant_id=1)
    assert found["mode"] == "external" and found["results"][0]["external"] and "twelve months" in found["results"][0]["text"]
    spoken = await engine.search("what does the warranty cover", tenant_id=1)
    assert spoken.startswith("[warranty.pdf]") and "twelve months" in spoken
    assert (await engine.search_ranked("what does the warranty cover"))["results"] == []        # no org: unchanged behaviour


async def test_without_an_organization_retrieval_is_exactly_as_before(tmp_path):
    engine = engine_with_docs(tmp_path)
    await ingest(engine, tmp_path, "printer.md", "# Printer\n\nFor a paper jam open the rear cover and remove the paper.")
    engine.vector_status["ready"] = True
    result = await engine.search_ranked("my printer has a paper jam")
    assert result["results"] and result["mode"] in ("hybrid", "keyword") and "external" not in result["timings_ms"]
