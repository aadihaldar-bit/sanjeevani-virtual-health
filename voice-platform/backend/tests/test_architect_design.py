"""Architect designs an agent: desks, the documents they read, the voice. These tests check what the platform guarantees whatever the model
designs: a graph a call can run (the same contract the real Sanjeevani agent keeps), the safety rules in every step, no tool a step does not
have, documents written only into the person's own company, and nothing half-made when a save fails."""
import copy
import io
import json
import re
import types

import pytest
from sqlalchemy import select

from org_env import env  # noqa: F401
from test_tenant_isolation import ACME_BOSS, GLOBEX_BOSS, world  # noqa: F401
from app import architect_design as design_mod, architect_model
from app.architect_design import FRONT, assemble, design_prompt, document_prompt, tidy_design
from app.gemini_graph import KNOWLEDGE_TOOL, ROUTE_TOOL, build_graph
from app.gemini_live import CARING_VOICES, EXTENDED_MODEL, VOICES
from app.models import Workflow, WorkflowEdge, WorkflowNode
from app.tool_executor import ToolExecutor
from app.workflow_validation import validate_workflow

pytestmark = pytest.mark.asyncio

HOSPITAL = {
    "business": "Al Noor Clinic", "summary": "Books visits and answers billing questions.", "care": True, "emergency_number": "112",
    "languages": ["English", "Hindi"], "voice": "Sulafat",
    "greeting": "Hello, this is the Al Noor Clinic assistant, an automated assistant. Would you like English or Hindi?",
    "agent": None,
    "desks": [
        {"key": "appointments", "name": "Appointment desk", "does": "booking a visit with a doctor",
         "when": "The caller wants to book, change or ask about an appointment.",
         "prompt": "1. Ask what the visit is for. If they describe a problem, say you are sorry first.\n2. Ask which doctor or specialty.\n"
                   "3. Ask for their name, phone number and the day and time they prefer.\n4. Read it all back and say the team will confirm the slot.",
         "documents": ["doctors", "faq"], "also": ["billing"]},
        {"key": "billing", "name": "Billing desk", "does": "bills, fees and insurance", "when": "The caller asks about a bill, a fee or insurance.",
         "prompt": "1. Ask what the question is about.\n2. Answer from the documents.\n3. Call search_knowledge for anything about fees.\n"
                   "4. If it is not there, take their name and number so billing can call back.",
         "documents": ["fees"], "also": []},
    ],
    "documents": [
        {"key": "doctors", "name": "Doctors and timings", "about": "who the doctors are and when they see patients",
         "topics": ["cardiology", "paediatrics", "opening hours"], "existing": None},
        {"key": "fees", "name": "Fees and insurance", "about": "consultation fees and insurers", "topics": ["consultation fees", "insurance"], "existing": None},
        {"key": "faq", "name": "Common questions", "about": "common questions", "topics": ["parking", "reports"], "existing": "Clinic FAQ.md"},
        {"key": "unused", "name": "Nobody reads this", "about": "x", "topics": [], "existing": None},
    ],
    "notes": ["The team confirms every booking."],
}
SINGLE = {
    "business": "Lenovo Care", "summary": "Books laptop repairs.", "care": False, "languages": ["English"], "voice": "Charon",
    "greeting": "Hello, this is the Lenovo Care assistant, an automated assistant.",
    "agent": {"name": "Repair booking", "prompt": "1. Ask which laptop and what is wrong.\n2. Ask for a name and number.\n3. Read it back."},
    "desks": [], "documents": [{"key": "warranty", "name": "Warranty terms", "about": "the warranty", "topics": ["period"], "existing": None}], "notes": [],
}
FAQ = "Clinic FAQ.md"


def tidy(raw=None, existing=(FAQ,), brief=""):
    return tidy_design(copy.deepcopy(HOSPITAL if raw is None else raw), list(existing), brief)


def graph_of(design, doc_files=None):
    docs = doc_files if doc_files is not None else {d["key"]: d["existing"] or f"{d['name']}.md" for d in design["documents"]}
    parts = assemble(design, docs)
    rows = [types.SimpleNamespace(id=n["id"], type=n["type"], position_x=n["position"]["x"], position_y=n["position"]["y"], data_json=n["data"]) for n in parts["nodes"]]
    erows = [types.SimpleNamespace(source_node_id=e["source"], target_node_id=e["target"], label=e["label"], condition=e["condition"]) for e in parts["edges"]]
    return parts, rows, erows, build_graph(rows, erows)


# --- cleaning a design -----------------------------------------------------------------------------------------------------------

def test_a_design_is_tidied_into_what_the_console_shows_and_a_call_runs():
    d = tidy()
    assert [x["name"] for x in d["desks"]] == ["Appointment desk", "Billing desk"] and d["agent"] is None
    assert d["voice"] == "Sulafat" and d["care"] is True and d["languages"] == ["English", "Hindi"]
    assert [x["key"] for x in d["documents"]] == ["doctors", "fees", "faq"]            # the document nobody reads is not kept
    assert d["documents"][2]["existing"] == FAQ                                         # a document the company already has is reused
    assert d["desks"][0]["documents"] == ["doctors", "faq"] and d["desks"][0]["also"] == ["billing"]


def test_a_desk_may_name_its_documents_however_the_model_spelled_them():
    """The real model wrote keys like hospital_doctors in the plan and again on the desks; they did not match once one side was tidied."""
    raw = copy.deepcopy(HOSPITAL)
    raw["documents"][0]["key"], raw["documents"][1]["key"] = "hospital_doctors", "Billing Insurance"
    raw["desks"][0]["documents"] = ["hospital_doctors", "faq"]
    raw["desks"][1]["documents"] = ["billing_insurance", "Fees and insurance", "no_such_document"]          # by key (another spelling), then by name
    d = tidy_design(raw, [FAQ])
    assert [x["key"] for x in d["documents"]] == ["hospital-doctors", "billing-insurance", "faq"]
    assert d["desks"][0]["documents"] == ["hospital-doctors", "faq"] and d["desks"][1]["documents"] == ["billing-insurance"]


def test_instructions_never_name_a_tool():
    d = tidy()
    assert "search_knowledge" not in d["desks"][1]["prompt"]                            # the platform tells each step which tools it has
    assert "Answer from the documents" in d["desks"][1]["prompt"]                       # the rest of the steps stay


def test_an_existing_document_is_matched_by_its_exact_name_only():
    raw = copy.deepcopy(HOSPITAL)
    raw["documents"][2]["existing"] = "Made up.pdf"
    d = tidy_design(raw, [FAQ])
    assert d["documents"][2]["existing"] is None and d["documents"][2]["name"] == "Common questions"       # then it is planned as a new one
    assert tidy_design(HOSPITAL, ["clinic faq.md"])["documents"][2]["name"] == "clinic faq.md"             # case does not matter; the company's spelling wins


def test_a_voice_that_does_not_exist_becomes_one_that_does_and_a_health_agent_gets_a_gentle_one():
    raw = copy.deepcopy(HOSPITAL)
    raw["voice"] = "Cheerful Robot"
    assert tidy_design(raw, [FAQ])["voice"] in CARING_VOICES
    assert tidy(SINGLE)["voice"] == "Charon"
    bare = copy.deepcopy(SINGLE)
    bare["voice"] = "nobody"
    assert tidy(bare)["voice"] == "Aoede" and {v for v, _ in VOICES} >= set(CARING_VOICES)


def test_a_health_business_is_recognised_even_when_the_model_forgot_to_say_so():
    raw = copy.deepcopy(HOSPITAL)
    raw["care"] = False
    assert tidy_design(raw, [FAQ])["care"] is True                                      # the words say patients and a doctor
    assert tidy(SINGLE)["care"] is False


def test_one_desk_is_just_the_agent_and_two_or_more_are_desks():
    raw = copy.deepcopy(HOSPITAL)
    raw["desks"] = raw["desks"][:1]
    d = tidy_design(raw, [FAQ])
    assert d["desks"] == [] and d["agent"]["name"] == "Appointment desk" and "Ask what the visit is for" in d["agent"]["prompt"]
    assert len(tidy()["desks"]) == 2
    both = copy.deepcopy(HOSPITAL)
    both["agent"] = {"name": "Ignored", "prompt": "Ignored."}
    assert tidy_design(both, [FAQ])["agent"] is None                                    # desks win


def test_there_is_a_limit_to_desks_and_documents_and_the_front_desk_is_not_one_of_them():
    raw = copy.deepcopy(HOSPITAL)
    raw["desks"] = [{"key": f"d{i}", "name": f"Desk {i}", "prompt": "1. Help.", "documents": [], "also": ["d0", "nowhere", f"d{i}"]} for i in range(9)]
    raw["desks"].insert(0, {"key": "f", "name": "front DESK", "prompt": "1. Hijack."})
    raw["documents"] = [{"key": f"k{i}", "name": f"Doc {i}"} for i in range(12)]
    d = tidy_design(raw, [])
    assert len(d["desks"]) == design_mod.MAX_DESKS and FRONT.lower() not in [x["name"].lower() for x in d["desks"]]
    assert d["desks"][1]["also"] == ["d0"]                                              # a hand-over goes to a desk that exists, and not to itself
    assert len(tidy_design({**raw, "desks": [], "agent": {"name": "A", "prompt": "1. Help."}}, [])["documents"]) == design_mod.MAX_DOCS


@pytest.mark.parametrize("raw", [None, [], "text", {}, {"desks": []}, {"desks": [{"name": "Empty", "prompt": "  "}]},
                                 {"agent": {"name": "x", "prompt": "Call route_to and search_knowledge."}}])
def test_a_design_with_nothing_to_build_an_agent_from_is_refused(raw):
    with pytest.raises(ValueError):
        tidy_design(raw, [])


def test_the_extended_thinking_model_is_kept_only_when_asked_for_exactly():
    assert tidy({**HOSPITAL, "model": EXTENDED_MODEL, "thinking_level": "high"})["thinking_level"] == "HIGH"
    d = tidy({**HOSPITAL, "model": "models/anything", "thinking_level": "HIGH"})
    assert d["model"] == "" and d["thinking_level"] == ""


# --- the graph a call runs -------------------------------------------------------------------------------------------------------

def test_desks_become_a_front_desk_that_hands_the_caller_on_like_the_sanjeevani_agent():
    d = tidy()
    parts, rows, erows, g = graph_of(d)
    assert g is not None and g.nodes[g.entry_inbound].name == FRONT
    assert {t.name for t in g.targets(g.entry_inbound)} == {"Appointment desk", "Billing desk"}
    for node_id, node in g.nodes.items():
        if node.name != FRONT:
            assert any(t.name == FRONT for t in g.targets(node_id)), f"{node.name} cannot return the caller to the front desk"
    appt = next(n for n in g.nodes.values() if n.name == "Appointment desk")
    assert {t.name for t in g.targets(appt.id)} == {FRONT, "Billing desk"}               # and it may pass a caller who also needs billing
    assert g.nodes[g.entry_inbound].greeting == d["greeting"] and g.nodes[g.entry_inbound].voice == "Sulafat"
    assert g.when_to_route(g.entry_inbound, appt.id) == "The caller wants to book, change or ask about an appointment."


def test_the_generated_agent_passes_the_same_checks_as_the_real_one():
    d = tidy()
    parts, rows, erows, g = graph_of(d)
    known = {t["name"] for t in ToolExecutor().get_tool_definitions()} | {ROUTE_TOOL, KNOWLEDGE_TOOL}
    every_tool = {t["name"] for t in ToolExecutor().get_tool_definitions()} | {ROUTE_TOOL, "search_company_documents"}
    for node_id, node in g.nodes.items():
        assert all(t in known for t in g.tool_names(node_id)), node.name
        mine = set(g.tool_names(node_id))
        mentioned = {t for t in every_tool if re.search(rf"\b{re.escape(t)}\b", node.prompt)}
        assert mentioned <= mine, f"{node.name}'s instructions mention {sorted(mentioned - mine)}, which it cannot call"
        for dest in re.findall(r'route_to "([^"]+)"', node.prompt):
            assert g.target_named(node_id, dest) is not None, f"{node.name} hands over to '{dest}', which it is not connected to"
    nodes = [types.SimpleNamespace(id=n["id"], type=n["type"], data=n["data"]) for n in parts["nodes"]]
    edges = [types.SimpleNamespace(source=e["source"], target=e["target"]) for e in parts["edges"]]
    errors, warnings = validate_workflow(nodes, edges)
    assert errors == [] and warnings == []                                               # every step is joined to the rest


def test_each_desk_reads_only_its_own_documents_and_the_front_desk_none():
    d = tidy()
    _, _, _, g = graph_of(d)
    docs = {n.name: n.kb_docs for n in g.nodes.values()}
    assert docs == {FRONT: [], "Appointment desk": ["Doctors and timings.md", FAQ], "Billing desk": ["Fees and insurance.md"]}
    assert KNOWLEDGE_TOOL in g.tool_names(next(i for i, n in g.nodes.items() if n.name == "Billing desk"))
    assert KNOWLEDGE_TOOL not in g.tool_names(g.entry_inbound)


def test_every_step_carries_the_safety_rules_whatever_the_model_wrote():
    d = tidy()
    _, _, _, g = graph_of(d)
    for node in g.nodes.values():
        assert "NEVER INVENT" in node.prompt and "YOU CANNOT see a calendar" in node.prompt
        assert "EMERGENCIES COME FIRST" in node.prompt and "call 112" in node.prompt and "NEVER GIVE MEDICAL ADVICE" in node.prompt     # a health agent
        assert "LISTEN FIRST" in node.prompt and "SAFETY FIRST" not in node.prompt      # health care has its own, longer emergency rule
    assert g.care is True
    plain = tidy(SINGLE)
    text = assemble(plain, {"warranty": "Warranty terms.md"})["nodes"][1]["data"]["system_prompt"]
    assert "NEVER INVENT" in text and "EMERGENCIES" not in text                         # and a business that is not health care is not told about emergencies
    assert "NEVER GIVE MEDICAL ADVICE" not in text and "LISTEN FIRST" in text
    assert "SAFETY FIRST. If the caller says anyone is hurt, in danger or in an emergency" in text and "their local emergency number right now" in text     # but anyone hurt still comes first
    assert "tell them to call 112 right now" in assemble(tidy_design({**SINGLE, "emergency_number": "112"}, []), {})["nodes"][1]["data"]["system_prompt"]
    nodoc = assemble(tidy_design({**SINGLE, "documents": []}, []), {})["nodes"][1]["data"]["system_prompt"]
    assert "search_company_documents" not in nodoc                                      # no document, no instruction to look one up
    unspecified = tidy_design({**HOSPITAL, "emergency_number": ""}, [FAQ])
    assert "their local emergency number" in assemble(unspecified, {})["nodes"][1]["data"]["system_prompt"]


def test_the_front_desk_knows_when_to_send_a_caller_to_each_desk():
    d = tidy()
    front = assemble(d, {})["nodes"][1]["data"]
    assert front["role"] == "orchestrator" and front["tools"] == [] and front["use_rag"] is False
    assert "- The caller wants to book, change or ask about an appointment: Appointment desk" in front["system_prompt"]
    assert "- The caller asks about a bill, a fee or insurance: Billing desk" in front["system_prompt"]
    assert "booking a visit with a doctor, or bills, fees and insurance" in front["system_prompt"]
    assert "Listen for the language they choose" in front["system_prompt"]                                                  # two languages: the caller chooses
    assert "Listen for the language they choose" not in assemble(tidy_design({**HOSPITAL, "languages": ["English"]}, []), {})["nodes"][1]["data"]["system_prompt"]
    assert "you do not ask for names, dates of birth or phone numbers: the desks do that" in front["system_prompt"]       # a real caller was asked her child's birth date before being sent on


def test_a_desk_does_not_ask_again_for_what_the_caller_already_said():
    """On a test call a desk asked 'is anyone hurt?' three times after a hand-over, and another asked for a name before answering a question."""
    _, _, _, g = graph_of(tidy())
    desks = [n for n in g.nodes.values() if n.name != FRONT]
    assert len(desks) == 2 and all("taken over this call from another desk" in n.prompt and "skip any step whose answer the caller has already given" in n.prompt
                                   for n in desks)
    assert all("Never ask again for something they have already told you" in n.prompt for n in g.nodes.values())


def test_desks_do_their_own_part_first_and_never_hand_the_caller_straight_back():
    """On a test call two desks passed a caller who wanted a tow and a claim to each other, three times in a row."""
    _, _, _, g = graph_of(tidy())
    desks = [n for n in g.nodes.values() if n.name != FRONT]
    assert all("HANDING OVER. If the caller asks for more than one thing, do the part that is yours first" in n.prompt for n in desks)
    assert all("never straight back to the desk that sent them to you" in n.prompt and "the front desk only when no desk does" in n.prompt for n in desks)
    assert all("what is already done or known" in n.prompt for n in desks)
    assert "HANDING OVER" not in assemble(tidy(SINGLE), {})["nodes"][1]["data"]["system_prompt"]         # one agent has nobody to hand over to


def test_the_front_desk_passes_on_what_the_caller_already_told_it():
    front = assemble(tidy(), {})["nodes"][1]["data"]["system_prompt"]
    assert 'In "reason" put what they need and everything they have already told you (for example whether anyone is hurt, places, times)' in front


def test_a_caller_who_cannot_reach_the_emergency_number_is_told_to_ask_someone_nearby():
    text = assemble(tidy(SINGLE), {})["nodes"][1]["data"]["system_prompt"]
    assert "If they cannot get through, tell them to ask anyone nearby to call for them and to keep trying, and stay with them" in text
    assert "Give no medical instructions" in text
    _, _, _, g = graph_of(tidy())                                                       # health care has its own, longer emergency rule: the same help
    assert all("If they cannot get through, tell them to ask anyone nearby to call for them and to keep trying" in n.prompt for n in g.nodes.values())


def test_the_designer_is_told_to_settle_the_cases_that_could_go_to_two_desks():
    prompt = design_prompt([])
    assert '"when" is when the front desk sends a caller here: use the person\'s own words' in prompt and "a car stuck after an accident" in prompt


def test_a_caller_who_speaks_a_language_that_is_not_offered_is_told_what_is_and_asked_to_choose():
    """The languages offered stay in the greeting (a caller who says 'Hindi' gets Hindi); someone who starts in, say, Urdu is not guessed at."""
    front = assemble(tidy(), {})["nodes"][1]["data"]["system_prompt"]
    assert "If they speak a language that is not English or Hindi, say kindly, in simple words, that you can help in English or Hindi and ask which they would like" in front
    assert "which language they prefer if there are several" in design_prompt([])                                          # the greeting names the languages and asks


def test_a_single_agent_is_one_step_with_no_role_and_the_company_documents():
    d = tidy(SINGLE)
    parts, rows, erows, g = graph_of(d)
    assert g is None                                                                     # no role: a call runs it as one agent
    agent = next(n for n in parts["nodes"] if n["type"] == "agentNode")["data"]
    assert "role" not in agent and agent["tools"] == [] and agent["tools_explicit"] is True and agent["use_rag"] is True
    assert agent["kb_docs"] == ["Warranty terms.md"] and "search_company_documents" in agent["system_prompt"]
    assert agent["greeting"] == d["greeting"] and agent["voice"] == "Charon"
    assert [n["type"] for n in parts["nodes"]] == ["startNode", "agentNode", "endNode"]


def test_settings_follow_the_design():
    care = assemble(tidy(), {})["settings"]["gemini_live"]
    assert care["turn_end"] == "patient" and care["turn_start"] == "sensitive" and care["transcript_languages"] == ["en-US", "hi-IN"]
    assert assemble(tidy(SINGLE), {})["settings"] == {}                                  # nothing to change from the server's defaults
    slow = assemble(tidy({**SINGLE, "model": EXTENDED_MODEL}), {})["settings"]["gemini_live"]
    assert slow == {"model": EXTENDED_MODEL, "thinking_level": "LOW"}                    # the lowest level it accepts: a phone call cannot wait


# --- what the model is asked ----------------------------------------------------------------------------------------------------

def test_the_designer_is_told_the_limits_the_voices_and_what_the_company_already_has():
    system = design_prompt([{"name": FAQ, "title": "Clinic FAQ", "snippet": "Parking is free for patients."}])
    for expected in ("Google Calendar", "Run several", "Sulafat", "Vindemiatrix", FAQ, "Parking is free", "ONE JSON object", "Never name a tool"):
        if expected == "Run several":
            assert "several desks" not in system.split("WHAT AN AGENT CANNOT DO")[1].split("WHEN TO USE DESKS")[0]      # desks are not a limit any more
            continue
        assert expected in system, expected
    assert "(none yet)" in design_prompt([])


def test_a_document_is_written_from_facts_unless_a_demonstration_was_asked_for():
    doc = {"name": "Doctors and timings", "about": "the doctors", "topics": ["cardiology", "hours"]}
    facts = document_prompt("Al Noor Clinic", "A clinic in Pune.", doc, sample=False)
    assert "Never make up a name, a time, a price or a policy" in facts and "SAMPLE" not in facts and "cardiology; hours" in facts
    sample = document_prompt("Al Noor Clinic", "A clinic in Pune.", doc, sample=True)
    assert "SAMPLE details" in sample and "It is not real" in sample and "(sample)" in sample


# --- the endpoints -------------------------------------------------------------------------------------------------------------

class Model:
    def __init__(self, reply):
        self.reply, self.asked = reply, []

    async def ask(self, system_prompt, prompt, history=None, **kw):
        self.asked.append({"system": system_prompt, "prompt": prompt, **kw})
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply)


def answer_with(monkeypatch, reply):
    model = Model(reply)
    monkeypatch.setattr(architect_model, "ask", model.ask)
    return model


async def upload(world, user, name, text):
    return await world.client.post("/api/knowledge/upload", headers=world.as_user(user), files={"file": (name, io.BytesIO(text.encode()), "text/plain")})


async def acme_documents(world, user=ACME_BOSS):
    return [d["name"] for d in (await world.get("/api/knowledge/documents", user=user)).json()["documents"]]


async def test_a_design_is_made_with_the_thinking_model_and_the_companys_documents_in_view(world, monkeypatch):
    await upload(world, ACME_BOSS, FAQ, "# Clinic FAQ\n\n## Parking\n\nParking is free for patients and visitors of the clinic.")
    model = answer_with(monkeypatch, HOSPITAL)
    res = await world.post("/api/architect/design", user=ACME_BOSS, json={"brief": "A clinic with appointment and billing desks."})
    assert res.status_code == 200, res.text
    body = res.json()
    assert [d["name"] for d in body["design"]["desks"]] == ["Appointment desk", "Billing desk"] and body["existing_documents"] == [FAQ]
    assert len(body["voices"]) == len(VOICES) and body["design"]["documents"][2]["existing"] == FAQ
    asked = model.asked[0]
    assert asked["answer_tokens"] >= 4000 and asked["attempt_secs"] == design_mod.DESIGN_ATTEMPT_SECS and asked["total_secs"] == design_mod.DESIGN_TOTAL_SECS
    assert FAQ in asked["system"] and "Parking is free" in asked["system"] and "A clinic with appointment and billing desks." in asked["prompt"]


async def test_another_companys_documents_are_not_shown_to_the_designer(world, monkeypatch):
    await upload(world, GLOBEX_BOSS, "Globex secrets.md", "# Secrets\n\n## Plan\n\nThe Globex plan is to buy everything in sight next year.")
    model = answer_with(monkeypatch, HOSPITAL)
    assert (await world.post("/api/architect/design", user=ACME_BOSS, json={"brief": "A clinic."})).status_code == 200
    assert "Globex" not in model.asked[0]["system"]


async def test_sample_details_are_passed_on_to_the_designer(world, monkeypatch):
    model = answer_with(monkeypatch, HOSPITAL)
    await world.post("/api/architect/design", user=ACME_BOSS, json={"brief": "A clinic.", "sample_data": True})
    assert "made-up sample details" in model.asked[0]["prompt"]


@pytest.mark.parametrize("reply", [architect_model.ArchitectModelError("gemini-pro-latest ran out of room"), "I cannot do that.", {"desks": []}])
async def test_a_design_that_cannot_be_made_is_an_honest_error_not_a_made_up_agent(world, monkeypatch, reply):
    answer_with(monkeypatch, reply)
    res = await world.post("/api/architect/design", user=ACME_BOSS, json={"brief": "A clinic."})
    assert res.status_code == 502 and "could not design" in res.json()["detail"] and "gemini" not in res.json()["detail"]


async def test_only_an_administrator_may_ask_for_a_design(world, monkeypatch):
    answer_with(monkeypatch, HOSPITAL)
    await world.post(f"/api/tenants/{world.tenants['acme']}/members", json={"email": "ops@acme.test", "role": "operator"})
    assert (await world.post("/api/architect/design", user="ops@acme.test", json={"brief": "A clinic."})).status_code == 403
    assert (await world.post("/api/architect/design", user=ACME_BOSS, json={"brief": "x" * 1501})).status_code == 422


async def test_a_document_is_written_for_the_person_to_read(world, monkeypatch):
    model = answer_with(monkeypatch, "```markdown\n# Doctors and timings\n\n## Hours\n\nThe clinic is open every morning.\n```")
    res = await world.post("/api/architect/document", user=ACME_BOSS, json={"business": "Al Noor Clinic", "brief": "A clinic in Pune.", "sample_data": False,
                                                                               "document": {"key": "doctors", "name": "Doctors and timings", "about": "the doctors", "topics": ["hours"]}})
    assert res.status_code == 200, res.text
    assert res.json() == {"key": "doctors", "name": "Doctors and timings", "content": "# Doctors and timings\n\n## Hours\n\nThe clinic is open every morning."}
    asked = model.asked[0]
    assert asked["json_output"] is False and asked["models"] == design_mod.DOCUMENT_MODELS and asked["thinking"] == "LOW"
    assert asked["attempt_secs"] == design_mod.DOCUMENT_ATTEMPT_SECS and "A clinic in Pune." in asked["system"]


async def test_a_document_that_cannot_be_written_is_an_honest_error(world, monkeypatch):
    answer_with(monkeypatch, architect_model.ArchitectModelError("out of time"))
    doc = {"business": "X", "brief": "Y", "document": {"key": "a", "name": "A doc"}}
    res = await world.post("/api/architect/document", user=ACME_BOSS, json=doc)
    assert res.status_code == 502 and "could not write" in res.json()["detail"]
    nameless = await world.post("/api/architect/document", user=ACME_BOSS, json={**doc, "document": {"key": "a"}})
    assert nameless.status_code == 422


def build_body(**over):
    body = {"name": "Al Noor Clinic", "brief": "A clinic with appointment and billing desks.", "design": copy.deepcopy(HOSPITAL),
            "documents": [{"key": "doctors", "content": "# Doctors and timings\n\n## Hours\n\nThe clinic is open every morning from nine."},
                          {"key": "fees", "content": "# Fees and insurance\n\n## Fees\n\nThe team confirms the fee for each doctor."}]}
    body.update(over)
    return body


async def saved(world, name):
    async with world.maker() as db:
        wf = (await db.execute(select(Workflow).where(Workflow.name == name))).scalars().first()
        nodes = (await db.execute(select(WorkflowNode).where(WorkflowNode.workflow_id == wf.id))).scalars().all() if wf else []
        edges = (await db.execute(select(WorkflowEdge).where(WorkflowEdge.workflow_id == wf.id))).scalars().all() if wf else []
        return wf, nodes, edges


async def test_building_saves_the_documents_and_the_agent_that_reads_them_in_the_right_company(world):
    await upload(world, ACME_BOSS, FAQ, "# Clinic FAQ\n\n## Parking\n\nParking is free for patients and visitors of the clinic.")
    res = await world.post("/api/architect/build", user=ACME_BOSS, json=build_body())
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["desks"] == ["Appointment desk", "Billing desk"] and out["is_active"] is False and out["node_count"] == 5      # start, front desk, two desks, end
    assert {(d["name"], d["written_by_architect"]) for d in out["documents"]} == {
        ("Al Noor Clinic - Doctors and timings.md", True), ("Al Noor Clinic - Fees and insurance.md", True), (FAQ, False)}
    assert set(await acme_documents(world)) == {FAQ, "Al Noor Clinic - Doctors and timings.md", "Al Noor Clinic - Fees and insurance.md"}
    assert await acme_documents(world, GLOBEX_BOSS) == []                                    # the other company sees none of it

    wf, nodes, edges = await saved(world, "Al Noor Clinic")
    assert wf.tenant_id == world.tenants["acme"] and wf.is_active is False and wf.description == "A clinic with appointment and billing desks."
    assert wf.settings_json["gemini_live"]["turn_end"] == "patient" and wf.settings_json["gemini_live"]["transcript_languages"] == ["en-US", "hi-IN"]
    g = build_graph(nodes, edges)
    docs = {n.name: n.kb_docs for n in g.nodes.values()}
    assert docs["Appointment desk"] == ["Al Noor Clinic - Doctors and timings.md", FAQ] and docs["Billing desk"] == ["Al Noor Clinic - Fees and insurance.md"]
    assert g.care and g.nodes[g.entry_inbound].name == FRONT


async def test_what_the_person_edited_is_what_is_built_and_tools_are_never_taken_from_the_request(world):
    body = build_body()
    body["design"]["desks"][0]["prompt"] = "1. Ask for the patient's name.\n2. Call find_doctors for the specialty.\n3. Read it back."
    body["design"]["desks"][0]["tools"] = ["find_doctors", "book_doctor_appointment"]
    body["design"]["voice"] = "Achernar"
    body["documents"][0]["content"] = "# Doctors and timings\n\n## Hours\n\nEdited by the person: open every day."
    assert (await world.post("/api/architect/build", user=ACME_BOSS, json=body)).status_code == 200
    _, nodes, _ = await saved(world, "Al Noor Clinic")
    desk = next(n for n in nodes if (n.data_json or {}).get("name") == "Appointment desk").data_json
    assert "Ask for the patient's name" in desk["system_prompt"] and "find_doctors" not in desk["system_prompt"]       # edited text, no tool named
    assert desk["tools"] == [] and desk["tools_explicit"] is True and desk["voice"] == "Achernar"
    hits = await world.engine.search("open every day", node_docs=["Al Noor Clinic - Doctors and timings.md"], tenant_id=world.tenants["acme"])
    assert "Edited by the person" in hits                                                   # the document that was saved is the one the person edited


async def test_a_planned_document_the_person_did_not_keep_is_left_out_and_its_desk_goes_without(world):
    body = build_body()
    body["documents"] = [body["documents"][1]]                                              # the doctors document was never written
    assert (await world.post("/api/architect/build", user=ACME_BOSS, json=body)).status_code == 200
    _, nodes, edges = await saved(world, "Al Noor Clinic")
    g = build_graph(nodes, edges)
    appt = next(n for n in g.nodes.values() if n.name == "Appointment desk")
    assert appt.kb_docs == []                                                               # (the existing FAQ is not in this company's knowledge, but is kept by name)
    assert "Al Noor Clinic - Doctors and timings.md" not in await acme_documents(world)


async def test_building_twice_never_overwrites_a_document(world):
    assert (await world.post("/api/architect/build", user=ACME_BOSS, json=build_body())).status_code == 200
    second = await world.post("/api/architect/build", user=ACME_BOSS, json=build_body(name="Al Noor Clinic 2"))
    assert second.status_code == 200
    names = await acme_documents(world)
    assert "Al Noor Clinic - Doctors and timings.md" in names and "Al Noor Clinic - Doctors and timings (2).md" in names
    _, nodes, edges = await saved(world, "Al Noor Clinic 2")
    g = build_graph(nodes, edges)
    assert next(n for n in g.nodes.values() if n.name == "Billing desk").kb_docs == ["Al Noor Clinic - Fees and insurance (2).md"]      # its own copy


async def test_a_single_agent_is_built_the_same_way(world):
    body = build_body(name="Lenovo Care", brief="Books laptop repairs.", design=copy.deepcopy(SINGLE), documents=[{"key": "warranty", "content": "# Warranty terms\n\n## Period\n\nThe warranty lasts two years."}])
    res = await world.post("/api/architect/build", user=ACME_BOSS, json=body)
    assert res.status_code == 200, res.text
    wf, nodes, edges = await saved(world, "Lenovo Care")
    assert build_graph(nodes, edges) is None and res.json()["desks"] == []
    agent = next(n for n in nodes if n.type == "agentNode").data_json
    assert agent["kb_docs"] == ["Lenovo Care - Warranty terms.md"] and agent["use_rag"] is True
    assert wf.settings_json is None                                                          # nothing to change from the defaults


async def test_a_failed_save_leaves_neither_an_agent_nor_the_documents_written_for_it(world, monkeypatch):
    from app import models

    def failing(**fields):
        raise RuntimeError("disk full")

    monkeypatch.setattr(models, "WorkflowEdge", failing)
    res = await world.post("/api/architect/build", user=ACME_BOSS, json=build_body())
    assert res.status_code == 500 and "Nothing was saved" in res.json()["detail"]
    assert (await saved(world, "Al Noor Clinic"))[0] is None
    assert await acme_documents(world) == []                                                 # the documents it had written were taken out again


async def test_a_design_with_nothing_in_it_cannot_be_built(world):
    res = await world.post("/api/architect/build", user=ACME_BOSS, json=build_body(design={"desks": []}))
    assert res.status_code == 422 and "nothing to build" in res.json()["detail"].lower()


async def test_only_an_administrator_may_build(world):
    await world.post(f"/api/tenants/{world.tenants['acme']}/members", json={"email": "ops@acme.test", "role": "operator"})
    assert (await world.post("/api/architect/build", user="ops@acme.test", json=build_body())).status_code == 403
    assert (await saved(world, "Al Noor Clinic"))[0] is None
