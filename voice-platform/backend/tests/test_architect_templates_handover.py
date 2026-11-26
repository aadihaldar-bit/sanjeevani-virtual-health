"""Ready-made workflows, a company's own handover number, and the Architect assistant."""
import json

import pytest
from sqlalchemy import select

from org_env import ADMIN, LEGACY, env  # noqa: F401
from test_tenant_isolation import ACME_BOSS, GLOBEX_BOSS, world  # noqa: F401
from test_roles import MANAGER, OPERATOR, VIEWER, team  # noqa: F401
from app.config import get_config
from app.models import Workflow, WorkflowNode
from app.transfer import clean_number, handover_instruction, resolve_transfer_number

pytestmark = pytest.mark.asyncio

STL_ONLY = {"incident_management", "medical_receptionist", "brother_csr"}


# --- templates ---------------------------------------------------------------------------------------------------------

async def test_a_company_sees_generic_templates_and_never_stls_demos(world):
    mine = {t["id"] for t in (await world.get("/api/agents/templates", user=ACME_BOSS)).json()["templates"]}
    assert {"customer_support", "appointment_booking", "lead_qualification", "feedback_survey"} <= mine
    assert not (mine & STL_ONLY)
    staff = {t["id"] for t in (await world.get("/api/agents/templates", user=ADMIN)).json()["templates"]}
    assert STL_ONLY <= staff                                          # STL's own people keep their demos


async def test_a_company_cannot_create_an_agent_wired_to_stls_systems(world):
    for key in STL_ONLY:
        res = await world.post("/api/agents/", user=ACME_BOSS, json={"name": "Sneaky", "template": key})
        assert res.status_code == 403 and "STL" in res.text, key
    assert (await world.post("/api/agents/", user=ADMIN, json={"name": "STL desk", "template": "incident_management"})).status_code in (200, 201)


async def nodes_of(world, name):
    async with world.maker() as db:
        wf = (await db.execute(select(Workflow).where(Workflow.name == name))).scalars().first()
        rows = (await db.execute(select(WorkflowNode).where(WorkflowNode.workflow_id == wf.id))).scalars().all()
        return wf, [n.data_json or {} for n in rows]


async def test_a_template_takes_the_agents_name_and_belongs_to_the_company(world):
    res = await world.post("/api/agents/", user=ACME_BOSS, json={"name": "Acme Dental", "template": "customer_support"})
    assert res.status_code in (200, 201), res.text
    wf, nodes = await nodes_of(world, "Acme Dental")
    assert wf.tenant_id == world.tenants["acme"]
    prompts = " ".join(n.get("system_prompt", "") for n in nodes)
    assert "Acme Dental" in prompts and "{agent_name}" not in prompts
    assert any(n.get("toolName") == "Transfer to a person" for n in nodes)
    assert any(n.get("use_rag") is True for n in nodes)               # customer support answers from the knowledge base


async def test_stls_phone_number_never_appears_in_a_companys_workflow(world):
    await world.post("/api/agents/", user=ACME_BOSS, json={"name": "Acme Reminders", "template": "emi_reminder"})
    _, mine = await nodes_of(world, "Acme Reminders")
    assert not any("97643" in str(n.get("toolName", "")) for n in mine)
    await world.post("/api/agents/", user=ADMIN, json={"name": "STL Reminders", "template": "emi_reminder"})
    _, stl = await nodes_of(world, "STL Reminders")
    assert any("97643" in str(n.get("toolName", "")) for n in stl)    # STL's own copy is unchanged


async def test_every_template_in_the_gallery_really_exists(world):
    from app.agents_api import MULTI_AGENT_TEMPLATES, TEMPLATE_META
    assert set(TEMPLATE_META) <= set(MULTI_AGENT_TEMPLATES)
    for key, template in MULTI_AGENT_TEMPLATES.items():
        ids = {n["id"] for n in template["nodes"]}
        assert any(n["type"] == "startNode" for n in template["nodes"]), key
        assert all(e["source"] in ids and e["target"] in ids for e in template["edges"]), key


# --- handover number -----------------------------------------------------------------------------------------------------

async def test_a_company_sets_its_own_handover_number(world):
    acme = world.tenants["acme"]
    res = await world.put(f"/api/tenants/{acme}/handover", user=ACME_BOSS, json={"number": "98765 43210"})
    assert res.status_code == 200 and res.json()["handover_number"] == "+919876543210"
    listed = (await world.get("/api/tenants", user=ACME_BOSS)).json()["orgs"]
    assert listed[0]["handover_number"] == "+919876543210"
    assert (await world.put(f"/api/tenants/{acme}/handover", user=ACME_BOSS, json={"number": "not a number"})).status_code == 422
    cleared = await world.put(f"/api/tenants/{acme}/handover", user=ACME_BOSS, json={"number": ""})
    assert cleared.json()["handover_number"] is None


async def test_only_that_companys_admin_may_change_its_handover_number(team):
    acme = team.tenants["acme"]
    assert (await team.put(f"/api/tenants/{acme}/handover", user=GLOBEX_BOSS, json={"number": "+919800000001"})).status_code == 403
    for person in (VIEWER, OPERATOR, MANAGER):
        assert (await team.put(f"/api/tenants/{acme}/handover", user=person, json={"number": "+919800000001"})).status_code == 403
    assert (await team.put(f"/api/tenants/{acme}/handover", user=ADMIN, json={"number": "+919800000002"})).status_code == 200


async def test_a_call_is_only_ever_put_through_to_the_right_number(world):
    acme, globex, stl = world.tenants["acme"], world.tenants["globex"], world.tenants["stl"]
    platform = get_config().telephony.supervisor_transfer_number
    await world.put(f"/api/tenants/{acme}/handover", user=ACME_BOSS, json={"number": "+919876543210"})
    assert await resolve_transfer_number(acme) == "+919876543210"           # a company's own number
    assert await resolve_transfer_number(globex) is None                    # none set: never STL's line
    assert await resolve_transfer_number(stl) == platform                   # STL's own agents keep today's behaviour
    assert await resolve_transfer_number(None) == platform                  # an agent with no company is the platform's
    assert await resolve_transfer_number(globex, "+911112223334") == "+911112223334"      # a number set on the step wins
    assert await resolve_transfer_number(99999) is None                     # an unknown company is never put through


async def test_the_agent_is_told_what_to_do_in_each_case():
    assert "TRANSFER_HUMAN: +919876543210" in handover_instruction("+919876543210")
    without = handover_instruction(None)
    assert "TRANSFER_HUMAN:" not in without.replace("never output TRANSFER_HUMAN", "") and "call them back" in without


def test_phone_numbers_are_tidied_or_refused():
    assert clean_number("98765 43210") == "+919876543210"
    assert clean_number("+1 (415) 555-0123") == "+14155550123"
    assert clean_number("") is None and clean_number(None) is None
    for bad in ("abc", "+0123", "12"):
        with pytest.raises(ValueError):
            clean_number(bad)


# --- Architect -----------------------------------------------------------------------------------------------------------

class FakeModel:
    """Stands in for the thinking model: answers `reply` (or raises it) and remembers how it was asked."""
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    async def ask(self, system_prompt, prompt, history=None, *, cfg=None, answer_tokens=700):
        self.calls.append({"prompt": prompt, "history": history or [], "system": system_prompt, "cfg": cfg, "answer_tokens": answer_tokens})
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


@pytest.fixture
def ask(world, monkeypatch):
    async def run(user, reply, messages=None):
        engine = FakeModel(reply)
        monkeypatch.setattr("app.architect_model.ask", engine.ask)
        body = {"messages": messages or [{"role": "user", "content": "I run a dental clinic and want an agent to book visits"}]}
        res = await world.post("/api/architect/chat", user=user, json=body)
        return res, engine
    return run


def model(reply, action=None):
    return json.dumps({"reply": reply, "action": action})


async def test_architect_proposes_a_template_and_changes_nothing_itself(ask, world):
    res, engine = await ask(ACME_BOSS, model("A booking assistant fits well.", {"type": "create_agent", "name": "Dental booking", "template": "appointment_booking"}))
    body = res.json()
    assert res.status_code == 200 and body["reply"].startswith("A booking assistant")
    assert body["action"] == {"type": "create_agent", "name": "Dental booking", "template": "appointment_booking", "template_title": "Appointment booking"}
    async with world.maker() as db:
        assert (await db.execute(select(Workflow).where(Workflow.name == "Dental booking"))).first() is None       # it only proposed


async def test_architect_can_build_something_no_template_covers(ask):
    res, _ = await ask(ACME_BOSS, model("I will build that for you.", {"type": "create_agent", "name": "Pet clinic", "template": "custom_ai", "prompt": "Answer for a vet clinic."}))
    assert res.json()["action"] == {"type": "create_agent", "name": "Pet clinic", "template": "custom_ai", "prompt": "Answer for a vet clinic.", "sample_data": False}


async def test_architect_never_offers_stls_templates_to_a_company(ask):
    res, engine = await ask(ACME_BOSS, model("Using the clinic one.", {"type": "create_agent", "name": "Clinic", "template": "medical_receptionist", "prompt": "Books patients."}))
    action = res.json()["action"]
    assert action["template"] == "custom_ai" and "template_title" not in action                         # downgraded to a description
    assert "incident_management" not in engine.calls[0]["system"] and "medical_receptionist" not in engine.calls[0]["system"]
    res, _ = await ask(ACME_BOSS, model("Ok.", {"type": "create_agent", "name": "Clinic", "template": "medical_receptionist"}))
    assert res.json()["action"]["template"] == "blank"                                                  # nothing to build it from
    res, engine = await ask(ADMIN, model("Ok.", {"type": "create_agent", "name": "Clinic", "template": "medical_receptionist"}))
    assert res.json()["action"]["template"] == "medical_receptionist" and "medical_receptionist" in engine.calls[0]["system"]


async def test_architect_opens_pages_only_from_the_known_list(ask):
    res, _ = await ask(ACME_BOSS, model("Here are your calls.", {"type": "open_page", "page": "calls"}))
    assert res.json()["action"] == {"type": "open_page", "page": "calls", "tab": "completed", "label": "Call history"}
    res, _ = await ask(ACME_BOSS, model("Sure.", {"type": "open_page", "page": "../../etc"}))
    assert res.json()["action"] is None
    res, _ = await ask(ACME_BOSS, model("Sure.", {"type": "delete_everything"}))
    assert res.json()["action"] is None


async def test_architect_copes_with_messy_model_output(ask):
    fenced = "Sure!\n```json\n" + model("Fenced reply.", None) + "\n```"
    assert (await ask(ACME_BOSS, fenced))[0].json() == {"reply": "Fenced reply.", "action": None}
    plain = (await ask(ACME_BOSS, "Just some words, not JSON."))[0].json()
    assert plain == {"reply": "Just some words, not JSON.", "action": None}
    assert (await ask(ACME_BOSS, model("x" * 5000)))[0].json()["reply"] == "x" * 700


async def test_architect_reports_when_the_model_is_unavailable(ask):
    res, _ = await ask(ACME_BOSS, RuntimeError("provider down"))
    body = res.json()
    assert res.status_code == 200 and body["unavailable"] is True and body["action"] is None and "template" in body["reply"]


async def test_architect_remembers_the_conversation_and_validates_input(ask, world):
    convo = [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "What is the agent for?"}, {"role": "user", "content": "Bookings"}]
    res, engine = await ask(ACME_BOSS, model("Ok."), convo)
    assert engine.calls[0]["prompt"] == "Bookings" and [m["role"] for m in engine.calls[0]["history"]] == ["user", "assistant"]
    assert (await world.post("/api/architect/chat", user=ACME_BOSS, json={"messages": []})).status_code == 422
    assert (await world.post("/api/architect/chat", user=ACME_BOSS, json={"messages": [{"role": "user", "content": "x"}] * 21})).status_code == 422
    assert (await world.post("/api/architect/chat", user=ACME_BOSS, json={"messages": [{"role": "system", "content": "x"}]})).status_code == 422


async def test_only_administrators_may_use_architect(team, monkeypatch):
    engine = FakeModel(model("Hello."))
    monkeypatch.setattr("app.architect_model.ask", engine.ask)
    body = {"messages": [{"role": "user", "content": "Hello"}]}
    for person in (VIEWER, OPERATOR, MANAGER):
        assert (await team.post("/api/architect/chat", user=person, json=body)).status_code == 403
    assert (await team.post("/api/architect/chat", user=ACME_BOSS, json=body)).status_code == 200
    assert (await team.post("/api/architect/chat", user=LEGACY, json=body)).status_code == 200     # STL staff
    assert engine.calls and len(engine.calls) == 2                                               # refused callers never reached the model


async def test_architect_repairs_ids_whose_underscores_were_stripped(ask):
    """Some engines tidy replies for speech and turn create_agent into createagent. The proposal must survive that."""
    res, _ = await ask(ACME_BOSS, model("Setting it up.", {"type": "createagent", "name": "Dental", "template": "appointmentbooking", "prompt": ""}))
    assert res.json()["action"]["template"] == "appointment_booking"
    res, _ = await ask(ACME_BOSS, model("Opening.", {"type": "openpage", "page": "knowledge"}))
    assert res.json()["action"]["page"] == "knowledge"


async def test_architect_gives_the_model_room_for_a_whole_reply_and_the_companys_own_keys(ask):
    _, engine = await ask(ACME_BOSS, model("Ok."))
    call = engine.calls[0]
    assert call["answer_tokens"] >= 1000                              # a reply and a brief of up to 1,500 characters, as JSON
    from app.config import AppConfig
    assert isinstance(call["cfg"], AppConfig)                         # the configuration with this company's own keys applied


async def test_architect_is_told_what_an_agent_cannot_do_and_what_it_cannot_do_itself(ask, monkeypatch):
    monkeypatch.setenv("GEMINI_LIVE_ENABLED", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_LIVE_WORKFLOW_IDS", raising=False)
    _, engine = await ask(ACME_BOSS, model("Ok."))
    system = engine.calls[0]["system"]
    for limit in ("Google Calendar", "Salesforce", "SMS", "while the call is live", "Remember a caller"):
        assert limit in system, limit
    for can in ("Run several desks", "starter documents", "Speak in a chosen voice"):               # what it now designs itself
        assert can in system, can
    assert "built by hand in the agent builder" not in system                                      # desks are no longer something it has to refuse
    for yourself in ("change or delete an agent that already exists", "upload documents", "make an agent live", "read calls and results"):
        assert yourself in system, yourself
    assert "closest agent that can be built" in system                 # it offers what can be built instead of promising the rest


async def test_the_handover_claim_follows_what_the_voice_engine_can_really_do(ask, monkeypatch):
    monkeypatch.setenv("GEMINI_LIVE_ENABLED", "false")
    monkeypatch.delenv("GEMINI_LIVE_WORKFLOW_IDS", raising=False)
    _, engine = await ask(ACME_BOSS, model("Ok."))
    system = engine.calls[0]["system"]
    assert "unless the company has set a handover number in Settings" in system and "while the call is live" not in system


async def test_architect_passes_on_a_request_for_sample_details_and_nothing_else(ask):
    asked = {"type": "create_agent", "name": "Demo clinic", "template": "custom_ai", "prompt": "A clinic with appointments and billing desks.", "sample_data": True}
    res, _ = await ask(ACME_BOSS, model("Setting it up with sample details.", asked))
    assert res.json()["action"]["sample_data"] is True
    res, _ = await ask(ACME_BOSS, model("Ok.", {**asked, "sample_data": "false"}))
    assert res.json()["action"]["sample_data"] is False
    res, _ = await ask(ACME_BOSS, model("Ok.", {"type": "create_agent", "name": "Support", "template": "customer_support", "sample_data": True}))
    assert "sample_data" not in res.json()["action"]                                  # only a described agent has documents to write


async def test_architect_ignores_placeholder_text_the_model_may_echo(ask):
    res, _ = await ask(ACME_BOSS, model("Ok.", {"type": "create_agent", "name": "Support", "template": "customer_support | custom_ai | blank", "prompt": "only for custom_ai"}))
    assert res.json()["action"] == {"type": "create_agent", "name": "Support", "template": "customer_support", "template_title": "Customer support"}
    res, _ = await ask(ACME_BOSS, model("Ok.", {"type": "create_agent", "name": "Odd", "template": "custom_ai", "prompt": "only for custom_ai"}))
    assert res.json()["action"]["template"] == "blank"                       # a placeholder is not a description to build from
