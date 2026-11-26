"""Architect's streamed run: the model's thinking, a checklist, and the items done one at a time. These tests check what the person sees
(the events, in order), what really happens (an agent and its documents, in the right company, nothing live), and what does not happen
when a model fails, a save fails or the person leaves."""
import asyncio
import copy
import json

import pytest
from sqlalchemy import select

from org_env import env  # noqa: F401
from test_architect_design import FAQ, HOSPITAL, acme_documents, saved, upload
from test_tenant_isolation import ACME_BOSS, GLOBEX_BOSS, world  # noqa: F401
from app import architect_model, architect_run, tenancy
from app.architect_api import Context, parse_reply
from app.config import get_config
from app.models import Workflow

pytestmark = pytest.mark.asyncio

PLAN_SYSTEM, DESIGN_SYSTEM, DOCUMENT_SYSTEM = "You are Architect, the built-in assistant", "senior conversation designer", "You write one reference document"
BRIEF = "A clinic with appointment and billing desks."
CUSTOM = {"type": "create_agent", "name": "Al Noor Clinic", "template": "custom_ai", "prompt": BRIEF}


class Stub:
    """Stands in for the model. `plan` is what Architect answers first, `design` the design, `documents` the text of each document by name (or an
    exception); `thoughts` are the summaries it shows while it works, by kind of question."""

    def __init__(self, plan, design=None, documents=None, thoughts=None, delay=0.0):
        self.plan, self.design, self.documents, self.thoughts, self.delay = plan, design, documents or {}, thoughts or {}, delay
        self.calls = []

    async def ask(self, system_prompt, prompt, history=None, *, on_thought=None, **kw):
        kind = ("plan" if PLAN_SYSTEM in system_prompt else "design" if DESIGN_SYSTEM in system_prompt else "document")
        name = system_prompt.split('Document: "', 1)[1].split('"', 1)[0] if kind == "document" else ""
        self.calls.append({"kind": kind, "name": name, "system": system_prompt, "prompt": prompt, "history": history, "on_thought": on_thought, **kw})
        if self.delay:
            await asyncio.sleep(self.delay)
        if on_thought:
            for said in self.thoughts.get(kind, []):
                await on_thought(said)
        reply = {"plan": self.plan, "design": self.design}.get(kind) if kind != "document" else self.documents.get(
            name, f"# {name}\n\n## Overview\n\nSomething useful about {name}, long enough to keep.")
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, str) else json.dumps(reply)

    def of(self, kind):
        return [c for c in self.calls if c["kind"] == kind]


def says(reply, action=None):
    return {"reply": reply, "action": action}


def use(monkeypatch, stub):
    monkeypatch.setattr(architect_model, "ask", stub.ask)
    return stub


async def run(world, text="I run a clinic and want an agent", user=ACME_BOSS, messages=None):
    res = await world.post("/api/architect/run", user=user, json={"messages": messages or [{"role": "user", "content": text}]})
    assert res.status_code == 200, res.text
    return [json.loads(line) for line in res.text.splitlines() if line.strip()]


def kinds(events):
    return [e["t"] for e in events]


def states_of(events):
    """Where every checklist item ended up, replaying the events the way the console does."""
    states = {}
    for e in events:
        if e["t"] == "plan":
            states = {s["id"]: "pending" for s in e["steps"]}
        elif e["t"] == "insert":
            states.update({s["id"]: "pending" for s in e["steps"]})
        elif e["t"] == "step":
            states[e["id"]] = e["status"]
    return states


def of(events, t, **match):
    return [e for e in events if e["t"] == t and all(e.get(k) == v for k, v in match.items())]


async def all_workflows(world, tenant_key="acme"):
    async with world.maker() as db:
        return (await db.execute(select(Workflow).where(Workflow.tenant_id == world.tenants[tenant_key]))).scalars().all()


# --- an agent described in words, built step by step ---------------------------------------------------------------------------

async def test_a_described_agent_is_thought_through_planned_and_built_one_item_at_a_time(world, monkeypatch):
    await upload(world, ACME_BOSS, FAQ, "# Clinic FAQ\n\n## Parking\n\nParking is free for patients and visitors of the clinic.")
    stub = use(monkeypatch, Stub(says("I will design a front desk with two desks and write the documents they need.", CUSTOM), design=HOSPITAL,
                                 thoughts={"plan": ["**Reading the request**\n\nA clinic with two jobs."], "design": ["**Splitting the jobs**\n\nBooking and billing differ."]}))
    events = await run(world)

    assert kinds(events)[0] == "thinking" and events[0]["text"].startswith("**Reading the request**") and "step" not in events[0]      # it thinks first
    reply = of(events, "reply")[0]
    assert "front desk" in reply["text"] and kinds(events).index("reply") < kinds(events).index("plan")                                 # then says what it will do
    plan = of(events, "plan")[0]
    assert [s["id"] for s in plan["steps"]] == ["look", "design", "build"] and plan["steps"][2]["title"] == "Create “Al Noor Clinic”"
    insert = of(events, "insert")[0]                                                  # the documents are found out by the design, and added to the list
    assert insert["before"] == "build" and [s["id"] for s in insert["steps"]] == ["doc-doctors", "doc-fees", "save"]

    assert of(events, "thinking", step="design")[0]["text"].startswith("**Splitting the jobs**")          # the design's own thinking belongs to its item
    design_done = of(events, "step", id="design", status="done")[0]
    assert design_done["result"]["kind"] == "design" and [d["name"] for d in design_done["result"]["desks"]] == ["Appointment desk", "Billing desk"]
    assert design_done["result"]["documents"][2] == {"name": FAQ, "about": "common questions", "existing": True}      # it reused the company's own document

    order = [(e["id"], e["status"]) for e in events if e["t"] == "step"]
    assert order.index(("look", "done")) < order.index(("design", "running")) < order.index(("design", "done")) < order.index(("doc-doctors", "running"))
    assert order.index(("doc-fees", "done")) < order.index(("save", "running")) < order.index(("save", "done")) < order.index(("build", "running")) < order.index(("build", "done"))
    assert states_of(events) == {k: "done" for k in ("look", "design", "doc-doctors", "doc-fees", "save", "build")}
    assert all(e["secs"] >= 0 for e in events if e["t"] == "step" and e["status"] == "done" and e["id"] != "look" or "secs" in e)

    end = of(events, "done")[0]
    assert end["ok"] is True and kinds(events)[-1] == "done" and "It is a draft" in end["summary"] and "only what you told me" in end["summary"]
    assert "Appointment desk" in end["summary"] and end["agent_id"] and len(end["documents"]) == 2 and "id" in end["documents"][0]
    assert "(id " in end["note"] and "not live" in end["note"]

    wf, nodes, _ = await saved(world, "Al Noor Clinic")                                # what was said was really done, in the right company
    assert wf.id == end["agent_id"] and wf.tenant_id == world.tenants["acme"] and wf.is_active is False
    assert set(await acme_documents(world)) == {FAQ, "Al Noor Clinic - Doctors and timings.md", "Al Noor Clinic - Fees and insurance.md"}
    assert await acme_documents(world, GLOBEX_BOSS) == [] and await all_workflows(world, "globex") != [] and len(await all_workflows(world)) == 2
    assert len(stub.of("plan")) == 1 and len(stub.of("design")) == 1 and len(stub.of("document")) == 2


async def test_the_planning_call_shows_its_thinking_and_the_documents_do_not(world, monkeypatch):
    stub = use(monkeypatch, Stub(says("Ok.", CUSTOM), design=HOSPITAL))
    await run(world)
    assert stub.of("plan")[0]["on_thought"] is not None and stub.of("design")[0]["on_thought"] is not None
    assert all(c.get("on_thought") is None for c in stub.of("document"))                # nothing to watch while a document is written
    assert all(c["attempt_secs"] > 40 for c in stub.of("design"))                       # a stream is not held to nginx's minute


async def test_the_company_sees_only_its_own_documents_in_front_of_architect(world, monkeypatch):
    await upload(world, ACME_BOSS, FAQ, "# Clinic FAQ\n\n## Parking\n\nParking is free for patients and visitors of the clinic.")
    await upload(world, GLOBEX_BOSS, "Globex secrets.md", "# Secrets\n\n## Plan\n\nThe Globex plan is to buy everything in sight next year.")
    stub = use(monkeypatch, Stub(says("Ok.")))
    await run(world)
    system = stub.of("plan")[0]["system"]
    assert FAQ in system and "Parking is free" in system and "Globex" not in system


async def test_what_the_person_said_before_goes_to_the_model_as_the_conversation(world, monkeypatch):
    stub = use(monkeypatch, Stub(says("Ok.")))
    await run(world, messages=[{"role": "user", "content": "hello"}, {"role": "assistant", "content": "Hi, what do you need?"},
                               {"role": "user", "content": "a dental agent"}])
    call = stub.of("plan")[0]
    assert call["prompt"] == "a dental agent" and [m["role"] for m in call["history"]] == ["user", "assistant"]


async def test_sample_details_asked_for_reach_the_designer_and_the_writers_and_the_summary_says_so(world, monkeypatch):
    stub = use(monkeypatch, Stub(says("Ok.", {**CUSTOM, "sample_data": True}), design=HOSPITAL))
    events = await run(world)
    assert "made-up sample details" in stub.of("design")[0]["prompt"] and all("SAMPLE details" in c["system"] for c in stub.of("document"))
    assert "made-up sample details" in of(events, "done")[0]["summary"] and "only what you told me" not in of(events, "done")[0]["summary"]
    assert of(events, "step", id="doc-doctors", status="done")[0]["result"]["sample"] is True


async def test_a_single_agent_with_no_documents_has_no_document_items(world, monkeypatch):
    single = {"business": "Lenovo Care", "summary": "Books repairs.", "care": False, "languages": ["English"], "voice": "Charon", "greeting": "Hello.",
              "agent": {"name": "Repairs", "prompt": "1. Ask which laptop.\n2. Ask for a name and number."}, "desks": [], "documents": [], "notes": ["The team confirms every repair."]}
    events = await run_with(world, monkeypatch, says("Ok.", {**CUSTOM, "name": "Repairs"}), single)
    assert of(events, "insert") == [] and states_of(events) == {"look": "done", "design": "done", "build": "done"}
    end = of(events, "done")[0]
    assert end["ok"] and "one agent" in end["summary"] and end["notes"] == ["The team confirms every repair."] and end["documents"] == []


async def run_with(world, monkeypatch, plan, design=None, user=ACME_BOSS, **kw):
    use(monkeypatch, Stub(plan, design=design, **kw))
    return await run(world, user=user)


# --- a template or a blank page ---------------------------------------------------------------------------------------------------

async def test_a_template_is_one_item_and_the_agent_is_a_draft_in_the_right_company(world, monkeypatch):
    events = await run_with(world, monkeypatch, says("A booking assistant fits well.", {"type": "create_agent", "name": "Dental bookings", "template": "appointment_booking"}))
    plan = of(events, "plan")[0]["steps"]
    assert len(plan) == 1 and "Dental bookings" in plan[0]["title"] and "template" in plan[0]["title"]
    assert states_of(events) == {"build": "done"}
    end = of(events, "done")[0]
    assert end["ok"] and end["agent_id"] and "draft" in end["summary"]
    wf = next(w for w in await all_workflows(world) if w.name == "Dental bookings")
    assert wf.id == end["agent_id"] and wf.is_active is False and wf.tenant_id == world.tenants["acme"]


async def test_a_blank_page_is_created_the_same_way(world, monkeypatch):
    events = await run_with(world, monkeypatch, says("Starting empty.", {"type": "create_agent", "name": "Scratch", "template": "blank"}))
    assert "blank page" in of(events, "plan")[0]["steps"][0]["title"] and of(events, "done")[0]["ok"]


# --- documents on their own ---------------------------------------------------------------------------------------------------------

WRITE = {"type": "write_knowledge", "business": "Al Noor Clinic",
         "documents": [{"name": "Fees and insurance", "about": "fees and insurers", "topics": ["consultation fees", "insurance"]},
                       {"name": "Refunds", "about": "when a refund is given", "topics": ["refund policy"]}]}


async def test_documents_asked_for_on_their_own_are_written_from_the_persons_words_and_saved(world, monkeypatch):
    stub = use(monkeypatch, Stub(says("I will write both and save them into Knowledge.", WRITE),
                                 documents={"Fees and insurance": "# Fees and insurance\n\n## Fees\n\nThe consultation fee is five hundred rupees.",
                                            "Refunds": "# Refunds\n\n## Policy\n\nRefunds are made within seven days."}))
    events = await run(world, messages=[{"role": "user", "content": "Our consultation fee is 500 rupees and refunds take seven days."}])
    assert [s["id"] for s in of(events, "plan")[0]["steps"]] == ["look", "doc-1", "doc-2", "save"]
    assert states_of(events) == {"look": "done", "doc-1": "done", "doc-2": "done", "save": "done"}
    assert all("Our consultation fee is 500 rupees" in c["prompt"] or "Our consultation fee is 500 rupees" in c["system"] for c in stub.of("document"))
    end = of(events, "done")[0]
    assert end["ok"] and [d["name"] for d in end["documents"]] == ["Al Noor Clinic - Fees and insurance.md", "Al Noor Clinic - Refunds.md"]
    assert "Fees and insurance" in end["summary"] and "only what you told me" in end["summary"] and "fixed list" in end["summary"]
    assert set(await acme_documents(world)) == {"Al Noor Clinic - Fees and insurance.md", "Al Noor Clinic - Refunds.md"}
    assert await acme_documents(world, GLOBEX_BOSS) == []
    found = await world.engine.search("what is the consultation fee", tenant_id=world.tenants["acme"])
    assert "five hundred rupees" in found
    assert len(await all_workflows(world)) == 1                                       # no agent was made: only the starting one of the fixture


async def test_a_long_fact_sheet_reaches_the_document_writers_whole(world, monkeypatch):
    """A message holds up to 2,000 characters. The writers used to be given only the last 1,500, which silently lost the start of a long one."""
    first, last = "FIRST-FACT-ZEBRA", "LAST-FACT-PANDA"
    sheet = first + " " + "The fee is five hundred rupees. " * 58 + last
    assert 1800 < len(sheet) <= 2000
    stub = use(monkeypatch, Stub(says("Ok.", WRITE)))
    await run(world, messages=[{"role": "user", "content": sheet}])
    assert stub.of("document") and all(first in c["system"] and last in c["system"] for c in stub.of("document"))


async def test_the_facts_of_two_whole_messages_reach_the_writers(world, monkeypatch):
    one, two = "FIRST-MESSAGE-" + "a" * 1900, "SECOND-MESSAGE-" + "b" * 1900                  # 3,800 characters between them
    stub = use(monkeypatch, Stub(says("Ok.", WRITE)))
    await run(world, messages=[{"role": "user", "content": one}, {"role": "assistant", "content": "Noted."}, {"role": "user", "content": two}])
    assert stub.of("document") and all("FIRST-MESSAGE-" in c["system"] and "SECOND-MESSAGE-" in c["system"] for c in stub.of("document"))


async def test_a_document_without_a_company_name_is_saved_under_its_own_name(world, monkeypatch):
    events = await run_with(world, monkeypatch, says("Ok.", {**WRITE, "business": "", "documents": WRITE["documents"][:1]}))
    assert [d["name"] for d in of(events, "done")[0]["documents"]] == ["Fees and insurance.md"]


async def test_a_document_that_cannot_be_written_is_reported_and_the_others_are_still_saved(world, monkeypatch):
    events = await run_with(world, monkeypatch, says("Ok.", WRITE), documents={"Refunds": architect_model.ArchitectModelError("out of time")})
    assert states_of(events) == {"look": "done", "doc-1": "done", "doc-2": "failed", "save": "done"}
    end = of(events, "done")[0]
    assert end["ok"] and "could not write “Refunds”" in end["summary"] and len(end["documents"]) == 1
    assert await acme_documents(world) == ["Al Noor Clinic - Fees and insurance.md"]


async def test_when_no_document_can_be_written_nothing_is_saved_and_it_says_so(world, monkeypatch):
    boom = architect_model.ArchitectModelError("down")
    events = await run_with(world, monkeypatch, says("Ok.", WRITE), documents={"Fees and insurance": boom, "Refunds": boom})
    assert states_of(events)["save"] == "skipped"
    end = of(events, "done")[0]
    assert end["ok"] is False and end["retry"] is True and await acme_documents(world) == []


async def test_a_slow_message_bus_does_not_hold_up_the_end_of_a_run(world, monkeypatch):
    from app import knowledge_routes
    release, told = asyncio.Event(), []

    async def slow(payload):
        await release.wait()
        told.append(payload["document"]["name"])

    monkeypatch.setattr(knowledge_routes, "_broadcast", slow)
    events = await asyncio.wait_for(run_with(world, monkeypatch, says("Ok.", WRITE)), 10)
    assert of(events, "done")[0]["ok"] and told == []                                  # the answer is complete though the dashboards are not told yet
    release.set()
    for _ in range(100):
        if len(told) == 2:
            break
        await asyncio.sleep(0.02)
    assert sorted(told) == ["Al Noor Clinic - Fees and insurance.md", "Al Noor Clinic - Refunds.md"]          # ... and they are told, afterwards


async def test_documents_that_cannot_be_saved_are_all_taken_out_again(world, monkeypatch):
    from app import architect_design
    real, calls = architect_design._save_document, []

    async def second_fails(name, content):
        calls.append(name)
        if len(calls) == 2:
            raise RuntimeError("disk full")
        return await real(name, content)

    monkeypatch.setattr(architect_design, "_save_document", second_fails)
    events = await run_with(world, monkeypatch, says("Ok.", WRITE))
    assert states_of(events)["save"] == "failed" and of(events, "done")[0]["ok"] is False
    assert await acme_documents(world) == []                                          # the first one went in, and came out again


# --- showing the documents ---------------------------------------------------------------------------------------------------------------

async def test_showing_the_knowledge_lists_this_companys_documents_and_the_agents_that_read_them(world, monkeypatch):
    await upload(world, ACME_BOSS, FAQ, "# Clinic FAQ\n\n## Parking\n\nParking is free for patients and visitors of the clinic.")
    await upload(world, ACME_BOSS, "Fees.md", "# Fees\n\n## Fees\n\nThe consultation fee is five hundred rupees for every visit.")
    await upload(world, GLOBEX_BOSS, "Globex secrets.md", "# Secrets\n\n## Plan\n\nThe Globex plan is to buy everything in sight next year.")
    async with world.maker() as db:
        from app.models import WorkflowNode
        wf = (await db.execute(select(Workflow).where(Workflow.tenant_id == world.tenants["acme"]))).scalars().first()
        db.add(WorkflowNode(id=f"agent-{wf.id}", workflow_id=wf.id, type="agentNode", position_x=0, position_y=0, data_json={"name": "Front", "kb_docs": [FAQ]}))
        await db.commit()
        reader = wf.name
    events = await run_with(world, monkeypatch, says("You have two documents.", {"type": "show_knowledge"}))
    action = of(events, "action")[0]["action"]
    assert action["type"] == "show_knowledge" and sorted(d["name"] for d in action["documents"]) == [FAQ, "Fees.md"]
    by_name = {d["name"]: d for d in action["documents"]}
    assert by_name[FAQ]["read_by"] == [reader] and by_name["Fees.md"]["read_by"] == []
    assert by_name[FAQ]["id"] and by_name[FAQ]["sections"] >= 1 and "Globex" not in json.dumps(events)
    assert of(events, "plan") == [] and of(events, "done")[0]["ok"]                   # nothing to check off: it only shows them


async def test_a_company_with_no_documents_is_told_so_by_an_empty_list(world, monkeypatch):
    events = await run_with(world, monkeypatch, says("You have none yet.", {"type": "show_knowledge"}))
    assert of(events, "action")[0]["action"]["documents"] == []


# --- things left to the person -----------------------------------------------------------------------------------------------------------

async def test_a_change_of_voice_waits_for_the_person_and_nothing_is_changed(world, monkeypatch):
    async with world.maker() as db:
        before = [(w.id, w.name) for w in (await db.execute(select(Workflow).where(Workflow.tenant_id == world.tenants["acme"]))).scalars().all()]
    name = before[0][1]
    events = await run_with(world, monkeypatch, says("A gentler voice suits patients.", {"type": "change_speaking", "agent": name, "voice": "Sulafat"}))
    action = of(events, "action")[0]["action"]
    assert action["type"] == "change_speaking" and action["agent_id"] == before[0][0] and action["changes"] == {"voice": "Sulafat"}
    assert of(events, "plan") == [] and of(events, "done")[0]["ok"]
    assert [(w.id, w.name) for w in await all_workflows(world)] == before


async def test_a_page_to_open_is_offered_and_a_plain_answer_has_nothing_to_do(world, monkeypatch):
    events = await run_with(world, monkeypatch, says("Documents go in Knowledge.", {"type": "open_page", "page": "knowledge"}))
    assert of(events, "action")[0]["action"]["type"] == "open_page"
    plain = await run_with(world, monkeypatch, says("Happy to help. What kind of business is this for?"))
    assert of(plain, "action") == [] and of(plain, "plan") == [] and of(plain, "reply")[0]["text"].startswith("Happy to help") and kinds(plain)[-1] == "done"


# --- when something goes wrong ----------------------------------------------------------------------------------------------------------

async def test_when_no_model_can_answer_it_says_so_and_makes_nothing(world, monkeypatch):
    events = await run_with(world, monkeypatch, architect_model.ArchitectModelError("all down"))
    assert kinds(events)[-1] == "error" and "could not reach the assistant" in events[-1]["message"] and "done" not in kinds(events)
    assert await all_workflows(world) == [] or len(await all_workflows(world)) == 1


async def test_a_design_that_fails_makes_nothing_and_offers_to_try_again_or_make_a_simple_agent(world, monkeypatch):
    before = len(await all_workflows(world))
    events = await run_with(world, monkeypatch, says("Ok.", CUSTOM), architect_model.ArchitectModelError("out of time"))
    assert states_of(events) == {"look": "done", "design": "failed", "build": "pending"}
    end = of(events, "done")[0]
    assert end["ok"] is False and end["retry"] is True and end["fallback"] == {"name": "Al Noor Clinic", "prompt": BRIEF}
    assert len(await all_workflows(world)) == before and await acme_documents(world) == []


async def test_a_design_that_is_not_a_design_is_treated_the_same(world, monkeypatch):
    events = await run_with(world, monkeypatch, says("Ok.", CUSTOM), "this is not json at all")
    assert states_of(events)["design"] == "failed" and of(events, "done")[0]["ok"] is False


async def test_one_document_that_cannot_be_written_does_not_stop_the_agent_and_its_desk_goes_without(world, monkeypatch):
    events = await run_with(world, monkeypatch, says("Ok.", CUSTOM), HOSPITAL, documents={"Fees and insurance": architect_model.ArchitectModelError("down")})
    assert states_of(events)["doc-fees"] == "failed" and states_of(events)["doc-doctors"] == "done" and states_of(events)["build"] == "done"
    end = of(events, "done")[0]
    assert end["ok"] and "could not write “Fees and insurance”" in end["summary"]
    _, nodes, _ = await saved(world, "Al Noor Clinic")
    billing = next(n for n in nodes if n.id.startswith("desk-billing"))
    assert (billing.data_json or {}).get("kb_docs") == []                              # it reads nothing, and so says the team will follow up
    assert set(await acme_documents(world)) == {"Al Noor Clinic - Doctors and timings.md", "Al Noor Clinic - Common questions.md"}      # all but the one that failed


async def test_every_document_failing_still_builds_the_agent_without_saving_anything(world, monkeypatch):
    boom = architect_model.ArchitectModelError("down")
    events = await run_with(world, monkeypatch, says("Ok.", CUSTOM), HOSPITAL, documents={"Doctors and timings": boom, "Fees and insurance": boom, "Common questions": boom})
    states = states_of(events)
    assert states["save"] == "skipped" and states["build"] == "done" and of(events, "done")[0]["ok"]
    assert await acme_documents(world) == []


async def test_a_failed_save_leaves_neither_an_agent_nor_the_documents_and_each_item_says_what_happened(world, monkeypatch):
    from app import models

    def failing(**fields):
        raise RuntimeError("disk full")

    monkeypatch.setattr(models, "WorkflowEdge", failing)
    before = len(await all_workflows(world))
    events = await run_with(world, monkeypatch, says("Ok.", CUSTOM), HOSPITAL)
    states = states_of(events)
    assert states["save"] == "failed" and states["build"] == "failed"
    assert "Taken out again" in of(events, "step", id="save", status="failed")[0]["detail"]
    end = of(events, "done")[0]
    assert end["ok"] is False and end["retry"] is True and "Nothing was saved" in end["summary"]
    assert len(await all_workflows(world)) == before and await acme_documents(world) == []


async def test_something_unexpected_ends_the_run_with_an_error_and_no_item_is_left_running(world, monkeypatch):
    use(monkeypatch, Stub(says("Ok.", CUSTOM), design=HOSPITAL))
    original = architect_run.design_from_brief

    async def breaks(*a, **k):
        raise RuntimeError("a bug")

    monkeypatch.setattr(architect_run, "design_from_brief", breaks)
    events = await run(world)
    assert kinds(events)[-1] == "error" and "Nothing more was done" in events[-1]["message"]
    assert states_of(events)["design"] == "failed" and "running" not in states_of(events).values()
    monkeypatch.setattr(architect_run, "design_from_brief", original)


# --- who may, and how it is sent --------------------------------------------------------------------------------------------------------

async def test_only_an_administrator_may_ask_architect_to_work(world, monkeypatch):
    use(monkeypatch, Stub(says("Ok.", CUSTOM), design=HOSPITAL))
    for email, role in (("ops@acme.test", "operator"), ("view@acme.test", "viewer")):
        await world.post(f"/api/tenants/{world.tenants['acme']}/members", json={"email": email, "role": role})
        assert (await world.post("/api/architect/run", user=email, json={"messages": [{"role": "user", "content": "an agent"}]})).status_code == 403
    assert (await world.client.post("/api/architect/run", json={"messages": [{"role": "user", "content": "an agent"}]})).status_code == 401
    assert (await world.post("/api/architect/run", user=ACME_BOSS, json={"messages": []})).status_code == 422
    assert await acme_documents(world) == []


async def test_the_stream_is_one_json_object_per_line_that_nginx_passes_on_at_once(world, monkeypatch):
    use(monkeypatch, Stub(says("Ok.")))
    res = await world.post("/api/architect/run", user=ACME_BOSS, json={"messages": [{"role": "user", "content": "hi"}]})
    assert res.headers["content-type"].startswith("application/x-ndjson") and res.headers["x-accel-buffering"] == "no"
    assert "no-cache" in res.headers["cache-control"]
    lines = res.text.split("\n")
    assert lines[-1] == "" and all(isinstance(json.loads(line), dict) for line in lines[:-1])


async def test_a_quiet_stretch_is_filled_with_pings_so_a_proxy_does_not_give_up(world, monkeypatch):
    monkeypatch.setattr(architect_run, "HEARTBEAT_SECS", 0.05)
    use(monkeypatch, Stub(says("Ok."), delay=0.4))
    events = await run(world)
    assert kinds(events).count("ping") >= 3 and kinds(events)[-1] == "done"


async def test_another_company_never_sees_what_was_built_for_this_one(world, monkeypatch):
    await run_with(world, monkeypatch, says("Ok.", CUSTOM), HOSPITAL)
    globex = await run_with(world, monkeypatch, says("Ok.", {"type": "show_knowledge"}), user=GLOBEX_BOSS)
    assert of(globex, "action")[0]["action"]["documents"] == []
    assert "Al Noor" not in json.dumps(globex)


# --- the person leaves ------------------------------------------------------------------------------------------------------------------

def context():
    return Context({}, [], [], [], PLAN_SYSTEM, get_config())


def request(text="an agent"):
    from app.architect_api import ChatMessage, ChatRequest
    return ChatRequest(messages=[ChatMessage(role="user", content=text)])


async def test_leaving_stops_the_questions_put_to_the_model(world, monkeypatch):
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def slow(system_prompt, prompt, history=None, **kw):
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(architect_model, "ask", slow)
    stream = architect_run._stream(tenancy.TenantScope(world.tenants["acme"], "boss@acme.test", False, "tenant_admin"), context(), request())
    first = asyncio.ensure_future(stream.__anext__())
    await asyncio.wait_for(started.wait(), 5)
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)                               # the generator unwinds: its work is cancelled with it
    await asyncio.wait_for(cancelled.wait(), 5)                                       # the model call was cancelled, not left to run on


async def test_a_save_that_has_begun_finishes_even_if_the_person_leaves(world, monkeypatch):
    building, release, finished = asyncio.Event(), asyncio.Event(), []

    async def slow_build(db, name, brief, design, written, on_documents_saved=None):
        building.set()
        await release.wait()
        finished.append(name)
        return {"id": 99, "name": name, "desks": [], "documents": []}

    use(monkeypatch, Stub(says("Ok.", CUSTOM), design={**HOSPITAL, "documents": []}))
    monkeypatch.setattr(architect_run, "build_design", slow_build)
    stream = architect_run._stream(tenancy.TenantScope(world.tenants["acme"], "boss@acme.test", False, "tenant_admin"), context(), request())
    waiting = asyncio.ensure_future(_drain(stream))
    await asyncio.wait_for(building.wait(), 5)
    waiting.cancel()                                                                  # the person closes the page while the agent is being saved
    await asyncio.gather(waiting, return_exceptions=True)
    release.set()
    for _ in range(50):
        if finished:
            break
        await asyncio.sleep(0.02)
    assert finished == ["Al Noor Clinic"]


async def _drain(stream):
    async for _ in stream:
        pass


# --- what Architect may propose ----------------------------------------------------------------------------------------------------------

def parsed(action):
    return parse_reply(json.dumps({"reply": "Ok.", "action": action}), {})["action"]


def test_a_request_for_documents_keeps_only_documents_that_can_be_written():
    action = parsed({"type": "write_knowledge", "business": "  Al   Noor  ", "sample_data": "true", "documents": [
        {"name": "Fees", "about": "the fees", "topics": ["consultation", "", "insurance"]}, {"name": "fees"}, {"name": ""}, "junk", {"about": "no name"},
        {"name": "Refunds"}, {"name": "Hours"}, {"name": "Parking"}, {"name": "Too many"}]})
    assert action["type"] == "write_knowledge" and action["business"] == "Al Noor" and action["sample_data"] is True
    assert [d["name"] for d in action["documents"]] == ["Fees", "Refunds", "Hours", "Parking"]            # one name once, and at most four
    assert action["documents"][0] == {"name": "Fees", "about": "the fees", "topics": ["consultation", "insurance"]}


@pytest.mark.parametrize("action", [{"type": "write_knowledge"}, {"type": "write_knowledge", "documents": []}, {"type": "write_knowledge", "documents": ["x", {}]},
                                    {"type": "write_knowledge", "documents": "Fees"}])
def test_a_request_for_documents_with_nothing_to_write_is_no_action_at_all(action):
    assert parsed(action) is None


def test_showing_the_knowledge_needs_no_details_and_repairs_the_name():
    assert parsed({"type": "show_knowledge", "documents": ["ignored"]}) == {"type": "show_knowledge"}
    assert parsed({"type": "showknowledge"}) == {"type": "show_knowledge"}
    assert parsed({"type": "Show Knowledge"}) == {"type": "show_knowledge"}


async def test_the_answers_we_cannot_stand_behind_stay_out(world, monkeypatch):
    events = await run_with(world, monkeypatch, says("Ok.", {"type": "write_knowledge", "documents": [{"name": ""}]}))
    assert of(events, "plan") == [] and of(events, "action") == [] and of(events, "done")[0]["ok"]
    assert copy.deepcopy(events)
