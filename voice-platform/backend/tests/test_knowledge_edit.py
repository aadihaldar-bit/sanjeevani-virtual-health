"""Reading a knowledge document and changing its text: the engine keeps the keyword index and the vectors in step with the new text, only
what is indexed can be read, a built-in document is never rewritten, and a company sees and changes only its own documents."""
import io
import os

import pytest

from org_env import env  # noqa: F401
from test_rag_engine import make_engine, write
from test_tenant_isolation import ACME_BOSS, GLOBEX_BOSS, world  # noqa: F401
from app import rag_engine as rag_module
from app.rag_engine import MAX_EDIT_CHARS

pytestmark = pytest.mark.asyncio

FEES = "# Fees\n\n## Consultation\n\nThe consultation fee is five hundred rupees for every visit."


async def add(engine, tmp_path, name="Fees.md", text=FEES, tenant_id=None):
    return await engine.ingest_document(write(tmp_path, name, text), f"id-{name}", name, tenant_id=tenant_id)


# --- the engine ------------------------------------------------------------------------------------------------------------------

async def test_a_document_is_read_back_whole_with_its_name_and_title(tmp_path):
    engine = make_engine(tmp_path, vector=False)
    await add(engine, tmp_path)
    doc = engine.read_document("id-Fees.md")
    assert doc["content"] == FEES and doc["name"] == "Fees.md" and doc["title"] == "Fees" and doc["editable"] is True


async def test_changing_a_document_changes_what_search_finds_and_nothing_else(tmp_path):
    engine = make_engine(tmp_path, vector=False)
    await add(engine, tmp_path, tenant_id=7)
    before = engine._registry["id-Fees.md"]
    result = await engine.update_document_text("id-Fees.md", "# Fees\n\n## Consultation\n\nThe consultation fee is eight hundred rupees now.", tenant_id=7)
    assert result["name"] == "Fees.md" and result["chunks"] >= 1
    assert "eight hundred" in engine.read_document("id-Fees.md")["content"]
    assert (await engine.search("how much is the consultation fee", tenant_id=7)).count("eight hundred") >= 1
    assert "five hundred" not in await engine.search("how much is the consultation fee", tenant_id=7)
    after = engine._registry["id-Fees.md"]
    assert after["tenant_id"] == 7 and after["uploaded_at"] == before["uploaded_at"] and after["filename"] == "Fees.md" and after["edited_at"]
    assert [d["id"] for d in engine.list_documents(tenant_id=7)] == ["id-Fees.md"]


async def test_the_vectors_are_replaced_so_a_shorter_text_leaves_no_old_tail(tmp_path):
    engine = make_engine(tmp_path)
    long_text = "# Printers\n\n" + "\n\n".join(f"## Part {i}\n\n" + ("The printer paper jam needs the tray opened. " * 25) for i in range(4))
    await add(engine, tmp_path, "Printers.md", long_text)
    seen = []
    original = engine.vector.delete_doc

    async def spy(doc_id):
        seen.append(doc_id)
        return await original(doc_id)

    engine.vector.delete_doc = spy
    old_chunks = len([c for c in engine.chunks if c["doc_id"] == "id-Printers.md"])
    result = await engine.update_document_text("id-Printers.md", "# Printers\n\nOur warranty is twelve months.")
    assert old_chunks > 1 and result["chunks"] == 1 and result["semantic"] is True
    assert seen == ["id-Printers.md"]                                          # the old vectors were removed before the new ones went in
    assert "id-Printers.md" in await engine.vector.indexed_doc_ids()
    assert [c["doc_id"] for c in engine.chunks].count("id-Printers.md") == 1


async def test_a_vector_store_that_fails_still_leaves_the_new_text_searchable(tmp_path):
    engine = make_engine(tmp_path)
    await add(engine, tmp_path)

    async def broken(chunks):
        raise RuntimeError("vector store down")

    engine.vector.upsert_chunks = broken
    result = await engine.update_document_text("id-Fees.md", "# Fees\n\nThe fee is nine hundred rupees.")
    assert result["semantic"] is False and "id-Fees.md" not in engine._semantic_docs
    assert "nine hundred" in await engine.search("what is the fee")


async def test_text_without_a_heading_keeps_the_documents_title(tmp_path):
    engine = make_engine(tmp_path, vector=False)
    await add(engine, tmp_path)
    await engine.update_document_text("id-Fees.md", "Just a plain line about the fee.")
    assert engine.read_document("id-Fees.md")["content"].startswith("# Fees\n\nJust a plain line")


@pytest.mark.parametrize("text", ["", "   \n  ", "x" * (MAX_EDIT_CHARS + 1)], ids=["empty", "blank", "endless"])      # (an id of 200,000 letters breaks Windows)
async def test_empty_or_endless_text_is_refused_and_the_document_stays_as_it_was(tmp_path, text):
    engine = make_engine(tmp_path, vector=False)
    await add(engine, tmp_path)
    with pytest.raises(ValueError):
        await engine.update_document_text("id-Fees.md", text)
    assert engine.read_document("id-Fees.md")["content"] == FEES


async def test_a_built_in_document_can_be_read_but_never_rewritten(tmp_path):
    engine = make_engine(tmp_path, vector=False)
    (tmp_path / "kb" / "builtin.md").write_text("# Built in\n\nShipped with the product.", encoding="utf-8")
    engine._load_all_documents()
    assert engine.read_document("builtin.md")["editable"] is False
    assert await engine.update_document_text("builtin.md", "# Changed\n\nNo.") is None
    assert "Shipped with the product" in (tmp_path / "kb" / "builtin.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("doc_id", ["_registry.json", "../outside.md", "nothing-here", "", "kb/../_registry.json"])
async def test_only_a_document_that_is_indexed_can_be_read(tmp_path, doc_id):
    engine = make_engine(tmp_path, vector=False)
    await add(engine, tmp_path)
    (tmp_path / "outside.md").write_text("# Outside\n\nNot a knowledge document.", encoding="utf-8")
    assert engine.read_document(doc_id) is None


async def test_one_tenants_document_cannot_be_read_or_changed_by_another(tmp_path):
    engine = make_engine(tmp_path, vector=False)
    await add(engine, tmp_path, tenant_id=1)
    assert engine.read_document("id-Fees.md", tenant_id=2) is None
    assert await engine.update_document_text("id-Fees.md", "# Fees\n\nTaken over.", tenant_id=2) is None
    assert engine.read_document("id-Fees.md", tenant_id=1)["content"] == FEES


async def test_a_half_written_file_is_never_left_behind(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, vector=False)
    await add(engine, tmp_path)
    await engine.update_document_text("id-Fees.md", "# Fees\n\nNew text.")
    assert sorted(f for f in os.listdir(engine.kb_dir) if f.endswith(".tmp")) == []


# --- the routes --------------------------------------------------------------------------------------------------------------------

async def upload(world, user, name, text):
    res = await world.client.post("/api/knowledge/upload", headers=world.as_user(user), files={"file": (name, io.BytesIO(text.encode()), "text/plain")})
    assert res.status_code == 200, res.text
    return res.json()["document_id"]


async def test_a_company_reads_and_changes_its_own_document(world):
    doc_id = await upload(world, ACME_BOSS, "Fees.md", FEES)
    read = await world.get(f"/api/knowledge/documents/{doc_id}", user=ACME_BOSS)
    assert read.status_code == 200 and read.json()["content"] == FEES and read.json()["editable"] is True
    saved = await world.put(f"/api/knowledge/documents/{doc_id}", user=ACME_BOSS, json={"content": "# Fees\n\nThe fee is eight hundred rupees."})
    assert saved.status_code == 200 and saved.json()["status"] == "saved"
    assert "eight hundred" in (await world.get(f"/api/knowledge/documents/{doc_id}", user=ACME_BOSS)).json()["content"]
    found = (await world.post("/api/knowledge/search", user=ACME_BOSS, json={"query": "what is the fee"})).json()["results"]
    assert found and "eight hundred" in found[0]["text"]


async def test_another_company_gets_not_found_for_a_document_it_does_not_own(world):
    doc_id = await upload(world, ACME_BOSS, "Fees.md", FEES)
    assert (await world.get(f"/api/knowledge/documents/{doc_id}", user=GLOBEX_BOSS)).status_code == 404
    assert (await world.put(f"/api/knowledge/documents/{doc_id}", user=GLOBEX_BOSS, json={"content": "# Fees\n\nMine now."})).status_code == 404
    assert "Mine now" not in (await world.get(f"/api/knowledge/documents/{doc_id}", user=ACME_BOSS)).text


async def test_a_document_that_is_not_there_is_not_found_and_empty_text_is_refused(world):
    doc_id = await upload(world, ACME_BOSS, "Fees.md", FEES)
    assert (await world.get("/api/knowledge/documents/nope", user=ACME_BOSS)).status_code == 404
    assert (await world.put("/api/knowledge/documents/nope", user=ACME_BOSS, json={"content": "# A\n\nB"})).status_code == 404
    refused = await world.put(f"/api/knowledge/documents/{doc_id}", user=ACME_BOSS, json={"content": "   "})
    assert refused.status_code == 400 and "empty" in refused.json()["detail"]


async def test_the_team_may_read_a_document_but_only_an_administrator_changes_it(world):
    doc_id = await upload(world, ACME_BOSS, "Fees.md", FEES)
    for email, role in (("ops@acme.test", "operator"), ("view@acme.test", "viewer")):
        await world.post(f"/api/tenants/{world.tenants['acme']}/members", json={"email": email, "role": role})
        assert (await world.get(f"/api/knowledge/documents/{doc_id}", user=email)).status_code == 200
        assert (await world.put(f"/api/knowledge/documents/{doc_id}", user=email, json={"content": "# Fees\n\nChanged."})).status_code == 403
    assert (await world.get(f"/api/knowledge/documents/{doc_id}", user=ACME_BOSS)).json()["content"] == FEES
