"""An orchestrator and specialists on one Gemini Live call: the graph, and handing the caller from one step to the next."""
import asyncio
import types

import pytest

from app import gemini_live_processor as glp
from app.gemini_graph import build_graph
from app.gemini_live_processor import GeminiLiveProcessor
from app.tool_executor import ToolExecutor

pytestmark = pytest.mark.asyncio


def node(id_, type_="agentNode", y=0, **data):
    return types.SimpleNamespace(id=id_, type=type_, position_x=0, position_y=y, data_json=data)


def edge(src, dst, label="", condition=""):
    return types.SimpleNamespace(source_node_id=src, target_node_id=dst, label=label, condition=condition)


NODES = [
    node("start", "startNode", y=0, name="Start"),
    node("orch", y=1, name="Orchestrator", role="orchestrator", system_prompt="You are the front desk.", greeting="Hello from Sanjeevani.",
         tools=["mark_do_not_call"]),
    node("appt", y=2, name="Appointment Desk", role="specialist", system_prompt="You book doctors.", voice="Puck",
         tools=["find_doctors", "quote_visit_cost", "book_doctor_appointment"], kb_docs=["doctors.md"]),
    node("fb", y=3, name="Feedback Desk", role="specialist", system_prompt="You take feedback.",
         tools=["check_feedback_eligibility", "record_visit_feedback"]),
    node("out", y=4, name="Outbound care", role="outbound", system_prompt="You call patients.", tools=["confirm_identity"]),
    node("end", "endNode", y=5),
]
EDGES = [
    edge("start", "orch"),
    edge("orch", "appt", "Book a doctor", "The caller wants to see a doctor or has symptoms."),
    edge("orch", "fb", "Feedback", "The caller wants to give feedback on a visit."),
    edge("appt", "orch", "Back", "Anything else."),
    edge("fb", "orch", "Back"),
]


async def test_a_workflow_without_roles_runs_the_old_single_step_way():
    plain = [node("a", name="Receptionist", system_prompt="You help.")]
    assert build_graph(plain, []) is None


async def test_the_graph_knows_its_entries_and_who_can_hand_to_whom():
    g = build_graph(NODES, EDGES)
    assert (g.entry_inbound, g.entry_outbound) == ("orch", "out")
    assert [t.name for t in g.targets("orch")] == ["Appointment Desk", "Feedback Desk"]
    assert [t.name for t in g.targets("appt")] == ["Orchestrator"]
    assert g.targets("out") == []
    assert g.target_named("orch", "the appointment desk").id == "appt"
    assert g.target_named("orch", "Feedback Desk").id == "fb"
    assert g.target_named("orch", "Pharmacy") is None


async def test_each_step_may_use_only_its_own_tools_plus_search_and_handover():
    g = build_graph(NODES, EDGES)
    assert g.tool_names("orch") == ["mark_do_not_call", "route_to"]
    assert g.tool_names("appt") == ["find_doctors", "quote_visit_cost", "book_doctor_appointment", "search_knowledge", "route_to"]
    assert g.tool_names("fb") == ["check_feedback_eligibility", "record_visit_feedback", "route_to"]
    assert g.tool_names("out") == ["confirm_identity"]                       # nowhere to hand to: no handover tool


async def test_the_handover_tool_lists_the_places_and_when_to_use_each():
    decl = build_graph(NODES, EDGES).route_declaration("orch")
    assert decl["name"] == "route_to"
    assert "Appointment Desk: The caller wants to see a doctor" in decl["description"]
    assert "Feedback Desk: The caller wants to give feedback" in decl["description"]
    assert decl["parameters"]["required"] == ["destination", "reason"]
    assert build_graph(NODES, EDGES).route_declaration("out") is None


# ── the processor ──────────────────────────────────────────────────────────────────────────
class FakeLive:
    opened = []

    def __init__(self, *, model, system_instruction, modalities, voice, tools, vad, thinking_level, transcript_languages=None):
        self.system_instruction, self.voice, self.tools = system_instruction, voice, tools
        self.sent, self.responses, self.closed = [], [], False
        FakeLive.opened.append(self)

    async def connect(self):
        pass

    async def send_text(self, text, end_turn=True):
        self.sent.append(text)

    async def send_tool_response(self, responses):
        self.responses.append(responses)

    async def events(self):
        await asyncio.Event().wait()
        yield None

    async def close(self):
        self.closed = True


@pytest.fixture
def processor(monkeypatch):
    FakeLive.opened = []
    monkeypatch.setattr(glp, "GeminiLiveSession", FakeLive)
    g = build_graph(NODES, EDGES)
    p = GeminiLiveProcessor(system_instruction="x", greeting="Hello from Sanjeevani.", tool_executor=ToolExecutor(), graph=g,
                            entry_id="orch", call_context="CALLER: new patient.")
    return p


async def stop(p):
    for t in (p._recv_task, p._monitor_task):
        if t:
            t.cancel()
    await asyncio.sleep(0)


def names(session):
    return [t["name"] for t in session.tools]


async def test_the_call_starts_on_the_orchestrator_with_only_its_own_tools(processor):
    p = processor
    await p._start_graph()
    first = FakeLive.opened[0]
    assert "You are the front desk." in first.system_instruction and "CALLER: new patient." in first.system_instruction
    assert "You book doctors." not in first.system_instruction
    assert names(first) == ["mark_do_not_call", "route_to"]
    assert "Hello from Sanjeevani." in first.sent[0]
    await stop(p)


async def test_a_handover_opens_the_specialist_with_its_own_prompt_voice_tools_and_the_call_so_far(processor):
    p = processor
    await p._start_graph()
    old = p._session
    p._turns = [("agent", "Do you prefer English or Arabic?"), ("user", "English please, I have a fever")]
    await p._handoff("Appointment Desk", "wants a doctor for a fever", "English", old, {"id": "c1"})
    new = p._session
    assert new is not old and old.closed and p._node.id == "appt"
    assert "You book doctors." in new.system_instruction and "You are the front desk." not in new.system_instruction
    assert "taken over this call from Orchestrator" in new.system_instruction and "wants a doctor for a fever" in new.system_instruction
    assert "Caller: English please, I have a fever" in new.system_instruction
    assert "CALLER: new patient." in new.system_instruction                    # what we know about the number goes along
    assert new.voice == "Puck"
    assert names(new) == ["find_doctors", "quote_visit_cost", "book_doctor_appointment", "search_knowledge", "route_to"]
    assert "Handover" in new.sent[0]
    assert any(t[1] == "handoff" for t in p._diag.timeline)
    await stop(p)


async def test_a_step_cannot_hand_the_caller_straight_back_to_the_step_that_sent_them(processor):
    p = processor
    await p._start_graph()
    p._turns = [("agent", "Hello"), ("user", "I need a doctor")]
    await p._handoff("Appointment Desk", "a doctor", "", p._session, {"id": "c5"})
    appt = p._session
    await p._handoff("Orchestrator", "the doctor, again", "", appt, {"id": "c6"})
    assert p._session is appt and not appt.closed and p._node.id == "appt"          # nobody spoke in between: it stays
    assert "only just come to you from Orchestrator" in appt.responses[0][0]["response"]["result"]
    assert "do not pass the caller straight back to Orchestrator" in appt.system_instruction
    await stop(p)


async def test_the_caller_speaking_makes_a_hand_back_fine(processor):
    p = processor
    await p._start_graph()
    await p._handoff("Appointment Desk", "a doctor", "", p._session, {"id": "c7"})
    appt = p._session
    p._turns.append(("user", "Actually I want to give feedback instead"))
    await p._handoff("Orchestrator", "feedback", "", appt, {"id": "c8"})
    assert p._node.id == "orch" and appt.closed
    await stop(p)


async def test_words_the_caller_is_still_saying_count_as_speaking(processor):
    p = processor
    await p._start_graph()
    await p._handoff("Appointment Desk", "a doctor", "", p._session, {"id": "c9"})
    appt = p._session
    p._in_buf = "no wait, I"
    await p._handoff("Orchestrator", "something else", "", appt, {"id": "c10"})
    assert p._node.id == "orch"
    await stop(p)


async def test_the_call_so_far_that_the_next_step_gets_reaches_back_far_enough_for_a_long_call(processor):
    p = processor
    await p._start_graph()
    p._turns = [("user" if i % 2 else "agent", f"turn {i}") for i in range(36)]
    await p._handoff("Appointment Desk", "a doctor", "", p._session, {"id": "c11"})
    text = p._session.system_instruction
    assert "turn 0" in text and "turn 35" in text
    await stop(p)


async def test_a_handover_to_somewhere_that_does_not_exist_is_refused_and_the_call_stays_put(processor):
    p = processor
    await p._start_graph()
    old = p._session
    await p._handoff("Pharmacy", "wants medicine", "", old, {"id": "c2"})
    assert p._session is old and not old.closed and p._node.id == "orch"
    assert "Appointment Desk, Feedback Desk" in old.responses[0][0]["response"]["result"]
    await stop(p)


async def test_a_step_cannot_call_a_tool_that_belongs_to_another_step(processor):
    p = processor
    await p._start_graph()
    await p._handle_tool_calls([{"id": "t1", "name": "book_doctor_appointment", "args": {"doctor": "Dr. Sonia"}}], p._session)
    assert "not something you can do" in p._session.responses[0][0]["response"]["result"]
    await stop(p)


async def test_the_document_search_is_limited_to_the_steps_own_documents(processor, monkeypatch):
    p = processor
    seen = {}

    async def fake_execute(name, args):
        seen.update(name=name, node_docs=args.get("node_docs"))
        return {"result": "ok"}

    await p._start_graph()
    await p._handoff("Appointment Desk", "doctor", "", p._session, {"id": "c3"})
    monkeypatch.setattr(p._tool_executor, "execute", fake_execute)
    await p._handle_tool_calls([{"id": "t2", "name": "search_knowledge", "args": {"question": "cardiology hours"}}], p._session)
    assert seen == {"name": "search_knowledge", "node_docs": ["doctors.md"]}
    await stop(p)


async def test_route_to_in_a_tool_call_starts_the_handover_without_answering_the_old_session(processor):
    p = processor
    await p._start_graph()
    old = p._session
    await p._handle_tool_calls([{"id": "t3", "name": "route_to", "args": {"destination": "Feedback Desk", "reason": "feedback"}}], old)
    for _ in range(20):
        await asyncio.sleep(0.01)
        if p._node.id == "fb" and not p._handing_off:
            break
    assert p._node.id == "fb" and old.responses == []
    await stop(p)
