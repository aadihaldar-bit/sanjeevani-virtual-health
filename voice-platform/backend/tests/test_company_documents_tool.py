"""search_company_documents: an agent that is not a set of desks answers from its company's documents, and only its company's."""
import pytest

from test_sanjeevani_desk import TENANT, run, world  # noqa: F401
from app import knowledge_routes
from app.models import Call, CallStatus

pytestmark = pytest.mark.asyncio


class Rag:
    def __init__(self, found="Opening hours: nine to five, Monday to Saturday."):
        self.found, self.seen = found, []

    async def search(self, question, node_docs=None, tenant_id=None, top_k=3, **kw):
        self.seen.append({"question": question, "node_docs": node_docs, "tenant_id": tenant_id})
        if isinstance(self.found, Exception):
            raise self.found
        return self.found


async def test_the_search_is_limited_to_the_company_the_call_belongs_to(world, monkeypatch):
    te, _, ids = world
    rag = Rag()
    monkeypatch.setattr(knowledge_routes, "rag_engine", rag)
    out = await run(te, "search_company_documents", question="opening hours", call_id=ids["new_call"])
    assert "nine to five" in out
    assert rag.seen == [{"question": "opening hours", "node_docs": None, "tenant_id": TENANT}]     # no documents named: all of this company's


async def test_an_agent_given_documents_searches_only_those(world, monkeypatch):
    te, _, ids = world
    rag = Rag()
    monkeypatch.setattr(knowledge_routes, "rag_engine", rag)
    await run(te, "search_company_documents", question="price of a haircut", node_docs=["price list.pdf"], call_id=ids["new_call"])
    assert rag.seen[0]["node_docs"] == ["price list.pdf"]


async def test_a_call_with_no_company_searches_nothing(world, monkeypatch):
    """An unscoped search would reach every company's documents."""
    te, maker, _ = world
    rag = Rag()
    monkeypatch.setattr(knowledge_routes, "rag_engine", rag)
    async with maker() as db:
        orphan = Call(tenant_id=None, status=CallStatus.ACTIVE, source="smartflo-stream", caller_number="+971500000000", metadata_json={})
        db.add(orphan)
        await db.commit()
        orphan_id = orphan.id
    out = await run(te, "search_company_documents", question="opening hours", call_id=orphan_id)
    assert "don't have that" in out and rag.seen == []


@pytest.mark.parametrize("found", ["", "No relevant passages were found.", RuntimeError("the vector store is down")])
async def test_when_nothing_is_found_the_agent_is_told_to_say_so_and_take_a_message(world, monkeypatch, found):
    te, _, ids = world
    monkeypatch.setattr(knowledge_routes, "rag_engine", Rag(found))
    out = await run(te, "search_company_documents", question="warranty on a broken screen", call_id=ids["new_call"])
    assert "don't have that" in out and "take a message" in out


async def test_a_blank_question_is_asked_again_and_a_long_answer_is_kept_short(world, monkeypatch):
    te, _, ids = world
    monkeypatch.setattr(knowledge_routes, "rag_engine", Rag("x" * 5000))
    assert "What would you like me to look up" in await run(te, "search_company_documents", question="  ", call_id=ids["new_call"])
    assert len(await run(te, "search_company_documents", question="anything at all", call_id=ids["new_call"])) == 1800
