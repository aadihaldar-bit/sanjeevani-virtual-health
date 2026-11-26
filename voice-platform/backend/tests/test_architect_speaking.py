"""Architect explains an agent from facts, and changes how it speaks: its voice, its thinking setting, how it takes turns. A change is only
ever proposed for an agent that is really there, and applying one touches nothing else about the agent."""
import copy
import json
import types

import pytest
from sqlalchemy import select

from org_env import env  # noqa: F401
from test_tenant_isolation import ACME_BOSS, GLOBEX_BOSS, world  # noqa: F401
from test_architect_design import HOSPITAL, SINGLE, build_body, saved, answer_with
from app import architect_api
from app.architect_api import build_system_prompt, current_speaking, describe_agent, parse_reply
from app.gemini_live import EXTENDED_MODEL
from app.models import Workflow

pytestmark = pytest.mark.asyncio


def node(id_, name="", role="", voice="", docs=None, type_="agentNode", rag=True):
    data = {"name": name or id_, "role": role, "voice": voice, "kb_docs": docs or [], "use_rag": rag}
    return types.SimpleNamespace(id=id_, type=type_, data_json={k: v for k, v in data.items() if v not in ("", None)} if type_ == "agentNode" else {})


SANJEEVANI_LIKE = [node("s", type_="startNode"), node("f", "Front desk", "orchestrator", "Aoede"), node("a", "Appointment Desk", "specialist", "Aoede", ["doctors.md"]),
                node("p", "Pharmacy Desk", "specialist", "Aoede", ["pharmacy.md", "orders.md"]), node("o", "Patient care assistant", "outbound", "Aoede")]
LIVE = {"gemini_live": {"turn_end": "patient", "turn_start": "sensitive", "transcript_languages": ["en-US", "ar-XA"]}}


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("GEMINI_LIVE_ENABLED", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_LIVE_WORKFLOW_IDS", raising=False)


# --- explaining an agent from facts ----------------------------------------------------------------------------------------------

def test_an_agent_is_described_from_how_it_is_built(live):
    line = describe_agent("Sanjeevani patient care", True, LIVE, SANJEEVANI_LIKE, 11)
    assert line.startswith('- "Sanjeevani patient care" (id 11, live): a front desk with 2 desks (Appointment Desk, Pharmacy Desk)')
    assert "a step for calls it places (Patient care assistant)" in line and "Voice: Aoede" in line and "Thinking: standard" in line
    assert "ends a turn: patient" in line and "starts a turn: sensitive" in line
    assert "Reads: doctors.md, pharmacy.md, orders.md" in line


def test_the_description_says_what_each_desk_is_for_from_the_front_desks_arrows(live):
    arrow = lambda to, when: types.SimpleNamespace(source_node_id="f", target_node_id=to, label="", condition=when)           # noqa: E731
    edges = [arrow("a", "The caller wants to see a doctor, or to book an appointment."), arrow("p", "The caller wants to order medicines."),
             types.SimpleNamespace(source_node_id="a", target_node_id="f", label="", condition="Anything else.")]       # a way back is not what a desk is for
    line = describe_agent("Sanjeevani", True, LIVE, SANJEEVANI_LIKE, 11, edges)
    assert "Appointment Desk: for when the caller wants to see a doctor, or to book an appointment; Pharmacy Desk: for when the caller wants to order medicines)" in line
    assert "Anything else" not in line


def test_a_single_agent_a_default_voice_and_extended_thinking_are_each_said_plainly(live):
    one = describe_agent("Salon", False, {"gemini_live": {"model": EXTENDED_MODEL, "thinking_level": "MEDIUM"}}, [node("a", "Booking", rag=True)], 3)
    assert one.startswith('- "Salon" (id 3, not live): one agent.') and "(the default)" in one
    assert "Thinking: extended thinking (medium)" in one and "Turn-taking: the server defaults" in one
    assert "Reads: all of the company's documents" in one
    assert "Reads: no documents" in describe_agent("Bare", True, None, [node("a", rag=False)])


def test_where_the_older_pipeline_answers_the_voice_is_the_voice_engines(monkeypatch):
    monkeypatch.setenv("GEMINI_LIVE_ENABLED", "false")
    monkeypatch.delenv("GEMINI_LIVE_WORKFLOW_IDS", raising=False)
    line = describe_agent("Old", True, None, [node("a", "Support", voice="Aarohi")])
    assert "Voice engine voice: Aarohi" in line and "Thinking" not in line


def test_what_an_agent_speaks_with_today():
    assert current_speaking(LIVE, SANJEEVANI_LIKE) == {"voice": "Aoede", "model": "standard", "thinking_level": "", "turn_end": "patient", "turn_start": "sensitive"}
    silent = [node("a", "Support", voice="Aarohi")]                                    # a voice engine voice is not a Gemini voice
    assert current_speaking(None, silent)["voice"] == ""
    assert current_speaking({"gemini_live": {"model": EXTENDED_MODEL, "thinking_level": "HIGH"}}, silent)["model"] == "extended"


def test_the_prompt_carries_the_agents_the_voices_and_the_rules_for_changing_them(live):
    line = describe_agent("Sanjeevani patient care", True, LIVE, SANJEEVANI_LIKE, 11)
    system = build_system_prompt({}, ["Sanjeevani patient care"], [line])
    for expected in (line, "Understanding agents", "Sulafat (warm)", "extended thinking reasons longer", "patient waits longer",
                     '"type": "change_speaking"', "Do not invent anything that is not in it"):
        assert expected in system, expected
    assert "none yet" in build_system_prompt({}, [], None)
    assert "Al Noor" in build_system_prompt({}, ["Al Noor"])                              # without the facts, at least the names


# --- proposing a change ----------------------------------------------------------------------------------------------------------

AGENTS = [{"id": 11, "name": "Sanjeevani patient care", "current": {"voice": "Aoede", "model": "standard", "thinking_level": "", "turn_end": "patient", "turn_start": ""}},
          {"id": 12, "name": "Clinic", "current": {"voice": "", "model": "standard", "thinking_level": "", "turn_end": "", "turn_start": ""}},
          {"id": 13, "name": "clinic", "current": {"voice": "", "model": "standard", "thinking_level": "", "turn_end": "", "turn_start": ""}}]


def reply(**action):
    return json.dumps({"reply": "Done.", "action": {"type": "change_speaking", "agent": "Sanjeevani patient care", **action}})


def test_a_change_is_proposed_with_what_it_is_now_and_what_it_will_be():
    out = parse_reply(reply(voice="sulafat", turn_end="Quick", model="Extended thinking", thinking_level="high"), {}, AGENTS)["action"]
    assert out == {"type": "change_speaking", "agent_id": 11, "agent_name": "Sanjeevani patient care", "current": AGENTS[0]["current"],
                   "changes": {"voice": "Sulafat", "model": "extended", "thinking_level": "HIGH", "turn_end": "quick"}}


def test_only_what_really_changes_is_proposed():
    out = parse_reply(reply(voice="Aoede", turn_end="patient", turn_start="steady"), {}, AGENTS)["action"]
    assert out["changes"] == {"turn_start": "steady"}                                    # the voice and the turn setting are what it already has
    assert parse_reply(reply(voice="Aoede", turn_end="patient"), {}, AGENTS)["action"] is None


def test_words_the_platform_does_not_know_are_dropped():
    out = parse_reply(reply(voice="Kore", turn_end="instant", turn_start="loud", model="warp", thinking_level="MAX"), {}, AGENTS)["action"]
    assert out["changes"] == {"voice": "Kore"}                                          # the one real choice is kept; the made-up words are not
    assert parse_reply(reply(voice="Robot 9"), {}, AGENTS)["action"] is None


def test_a_thinking_level_means_nothing_without_extended_thinking():
    assert parse_reply(reply(thinking_level="HIGH"), {}, AGENTS)["action"] is None
    assert parse_reply(reply(model="extended", thinking_level="HIGH"), {}, AGENTS)["action"]["changes"] == {"model": "extended", "thinking_level": "HIGH"}


def test_an_agent_that_is_not_there_or_that_cannot_be_told_apart_is_never_changed():
    assert parse_reply(reply(voice="Kore", agent="Some other agent"), {}, AGENTS)["action"] is None
    assert parse_reply(reply(voice="Kore", agent="Clinic"), {}, AGENTS)["action"] is None                 # two agents share the name
    assert parse_reply(reply(voice="Kore", agent="Clinic (id 13)"), {}, AGENTS)["action"]["agent_id"] == 13
    assert parse_reply(reply(voice="Kore", agent="Clinic (id 99)"), {}, AGENTS)["action"] is None
    assert parse_reply(reply(voice="Kore", agent="SANJEEVANI  patient care"), {}, AGENTS)["action"]["agent_id"] == 11


# --- applying it -----------------------------------------------------------------------------------------------------------------

async def built(world, name="Al Noor Clinic", design=None, brief="A clinic."):
    res = await world.post("/api/architect/build", user=ACME_BOSS,
                           json=build_body(name=name, brief=brief, design=copy.deepcopy(design or HOSPITAL)))
    assert res.status_code == 200, res.text
    return res.json()["id"]


async def agent_state(world, name):
    wf, nodes, _ = await saved(world, name)
    return wf.settings_json, {(n.data_json or {}).get("name"): (n.data_json or {}).get("voice") for n in nodes if n.type == "agentNode"}


async def test_the_voice_changes_on_every_step_and_the_rest_of_the_agent_stays(world):
    agent_id = await built(world)
    res = await world.post("/api/architect/speaking", user=ACME_BOSS, json={"agent_id": agent_id, "voice": "vindemiatrix"})
    assert res.status_code == 200, res.text
    assert res.json()["speaking"]["voice"] == "Vindemiatrix" and res.json()["name"] == "Al Noor Clinic"
    settings, voices = await agent_state(world, "Al Noor Clinic")
    assert set(voices.values()) == {"Vindemiatrix"} and len(voices) == 3                             # the front desk and both desks
    assert settings["gemini_live"]["turn_end"] == "patient" and settings["gemini_live"]["transcript_languages"] == ["en-US", "hi-IN"]       # untouched


async def test_thinking_and_turn_taking_change_without_losing_each_other(world):
    agent_id = await built(world)
    ok = await world.post("/api/architect/speaking", user=ACME_BOSS, json={"agent_id": agent_id, "model": "extended", "thinking_level": "HIGH", "turn_start": "steady"})
    assert ok.status_code == 200 and ok.json()["speaking"] == {"voice": "Sulafat", "model": "extended", "thinking_level": "HIGH", "turn_end": "patient", "turn_start": "steady"}
    level = await world.post("/api/architect/speaking", user=ACME_BOSS, json={"agent_id": agent_id, "thinking_level": "MEDIUM"})
    assert level.json()["speaking"]["thinking_level"] == "MEDIUM" and level.json()["speaking"]["model"] == "extended"
    plain = await world.post("/api/architect/speaking", user=ACME_BOSS, json={"agent_id": agent_id, "model": "standard"})
    assert plain.json()["speaking"] == {"voice": "Sulafat", "model": "standard", "thinking_level": "", "turn_end": "patient", "turn_start": "steady"}
    settings, _ = await agent_state(world, "Al Noor Clinic")
    assert "model" not in settings["gemini_live"] and "thinking_level" not in settings["gemini_live"]


async def test_other_choices_of_the_agent_are_left_exactly_as_they_were(world):
    agent_id = await built(world)
    async with world.maker() as db:
        wf = (await db.execute(select(Workflow).where(Workflow.id == agent_id))).scalars().first()
        wf.settings_json = {**wf.settings_json, "stt": {"provider": "deepgram"}}
        await db.commit()
    assert (await world.post("/api/architect/speaking", user=ACME_BOSS, json={"agent_id": agent_id, "turn_end": "quick"})).status_code == 200
    settings, _ = await agent_state(world, "Al Noor Clinic")
    assert settings["stt"] == {"provider": "deepgram"} and settings["gemini_live"]["turn_end"] == "quick"


async def test_a_single_agent_changes_the_same_way(world):
    agent_id = await built(world, name="Lenovo Care", design=SINGLE, brief="Books laptop repairs.")
    res = await world.post("/api/architect/speaking", user=ACME_BOSS, json={"agent_id": agent_id, "voice": "Achird", "turn_end": "patient"})
    assert res.status_code == 200 and res.json()["speaking"]["voice"] == "Achird"
    settings, voices = await agent_state(world, "Lenovo Care")
    assert voices == {"Repair booking": "Achird"} and settings == {"gemini_live": {"turn_end": "patient"}}


@pytest.mark.parametrize("body", [{"voice": "Robot 9"}, {"turn_end": "instant"}, {"turn_start": "loud"}, {"thinking_level": "MAX"}, {"model": "warp"}])
async def test_a_choice_that_does_not_exist_is_refused_and_nothing_changes(world, body):
    agent_id = await built(world)
    before = await agent_state(world, "Al Noor Clinic")
    assert (await world.post("/api/architect/speaking", user=ACME_BOSS, json={"agent_id": agent_id, **body})).status_code == 422
    assert await agent_state(world, "Al Noor Clinic") == before


async def test_only_an_administrator_of_the_same_company_may_change_it(world):
    agent_id = await built(world)
    before = await agent_state(world, "Al Noor Clinic")
    await world.post(f"/api/tenants/{world.tenants['acme']}/members", json={"email": "ops@acme.test", "role": "operator"})
    assert (await world.post("/api/architect/speaking", user="ops@acme.test", json={"agent_id": agent_id, "voice": "Kore"})).status_code == 403
    assert (await world.post("/api/architect/speaking", user=GLOBEX_BOSS, json={"agent_id": agent_id, "voice": "Kore"})).status_code == 404       # not even there for them
    assert (await world.post("/api/architect/speaking", user=ACME_BOSS, json={"agent_id": 99999, "voice": "Kore"})).status_code == 404
    assert await agent_state(world, "Al Noor Clinic") == before


async def test_architect_explains_and_proposes_from_the_real_agents_of_the_company(world, monkeypatch):
    """Through the real chat route: the model is shown this company's agents (and only its own), and its proposal is checked against them."""
    monkeypatch.setenv("GEMINI_LIVE_ENABLED", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_LIVE_WORKFLOW_IDS", raising=False)
    agent_id = await built(world)
    seen = []

    async def ask(system_prompt, prompt, history=None, **kw):
        seen.append(system_prompt)
        return json.dumps({"reply": "A gentler voice suits patients.", "action": {"type": "change_speaking", "agent": "Al Noor Clinic", "voice": "Achernar"}})

    monkeypatch.setattr("app.architect_model.ask", ask)
    res = await world.post("/api/architect/chat", user=ACME_BOSS, json={"messages": [{"role": "user", "content": "Make my clinic agent sound gentler"}]})
    assert res.status_code == 200, res.text
    action = res.json()["action"]
    assert action["type"] == "change_speaking" and action["agent_id"] == agent_id and action["changes"] == {"voice": "Achernar"}
    assert action["current"]["voice"] == "Sulafat"                                        # what it speaks with today, so the card can show from and to
    assert (f'"Al Noor Clinic" (id {agent_id}, not live): a front desk with 2 desks (Appointment desk: for when the caller wants to book, change or ask about an appointment; '
            f'Billing desk: for when the caller asks about a bill, a fee or insurance)') in seen[0]
    assert "acme desk" in seen[0] and "globex desk" not in seen[0] and "stl desk" not in seen[0].replace("STL Digital", "")       # only this company's agents
    other = await world.post("/api/architect/chat", user=GLOBEX_BOSS, json={"messages": [{"role": "user", "content": "Make my clinic agent sound gentler"}]})
    assert other.json()["action"] is None                                                 # an agent of another company is not there for them
