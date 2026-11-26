"""How every agent sounds: kind and attentive, and gentler still when the call is about someone's health. The layer is added to the
instructions of every Gemini Live call, so it reaches the agents that already exist without anyone editing them."""
import importlib.util
import json
import os
import re
import types

import pytest

from app import agents_api
from app.gemini_graph import build_graph
from app.gemini_live_processor import GeminiLiveProcessor
from app.tool_executor import ToolExecutor
from app.voice_character import CARE, CARE_TOOLS, HUMAN, character, is_care

pytestmark = pytest.mark.asyncio

HERE = os.path.dirname(__file__)
SCRIPTS = os.path.join(HERE, "..", "..", "scripts")


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _template_text(key):
    nodes = [n["data"] for n in agents_api.MULTI_AGENT_TEMPLATES[key]["nodes"] if n["type"] == "agentNode"]
    return [f'{d.get("name", "")} {d.get("system_prompt", "")}' for d in nodes], [t for d in nodes for t in d.get("tools") or []]


# --- who is a health-care agent -----------------------------------------------------------------------------------------------

def test_the_medical_agents_that_exist_are_all_health_care():
    texts, tools = _template_text("medical_receptionist")
    assert is_care(*texts, tools=tools)
    sanjeevani = _load(os.path.join(SCRIPTS, "sanjeevani_prompts.py"), "sanjeevani_prompts_voice")
    for name in ("OUTBOUND", "ORCHESTRATOR", "APPOINTMENTS", "PHARMACY", "FEEDBACK"):
        assert is_care(getattr(sanjeevani, name)), f"the Sanjeevani {name.lower()} step is not recognised as health care"
    fixture = json.load(open(os.path.join(HERE, "..", "e2e", "fixtures", "medical_workflow.json"), encoding="utf-8"))
    nodes = [n["data"] for n in fixture["nodes"] if n.get("type") == "agentNode"]
    assert is_care(fixture.get("name", ""), *(f'{d.get("name", "")} {d.get("system_prompt", "")}' for d in nodes))


def test_agents_for_other_businesses_are_not():
    for key in ("incident_management", "brother_csr", "electricity_csr", "emi_reminder", "customer_support", "lead_qualification",
                "feedback_survey", "appointment_booking"):
        texts, tools = _template_text(key)
        assert not is_care(*texts, tools=tools), f"{key} is not about health"


def test_a_health_tool_or_a_word_like_clinic_is_enough_and_a_stray_word_is_not():
    assert is_care("You answer calls.", tools=["find_doctors"])
    assert is_care("You answer calls for Dr. Rao's dental clinic.")
    assert is_care("You help with health and treatment questions.")                 # two softer words together
    assert not is_care("You raise tickets for the IT team. Be in good health.")     # one soft word alone
    assert not is_care("")
    assert CARE_TOOLS >= {"find_doctor", "book_doctor_appointment", "start_medicine_order"}
    assert all(t in {d["name"] for d in ToolExecutor().get_tool_definitions()} for t in CARE_TOOLS)      # none of them is a made-up name


# --- what is added ------------------------------------------------------------------------------------------------------------

def test_every_agent_gets_the_human_layer_and_a_health_agent_gets_care_on_top():
    assert character(False) == HUMAN and character(True) == HUMAN + "\n\n" + CARE


def test_the_layer_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("HUMAN_VOICE_LAYER", "false")
    assert character(True) == "" and character(False) == ""


def test_the_layer_is_about_manner_only_and_stays_honest():
    text = HUMAN + CARE
    tools = {d["name"] for d in ToolExecutor().get_tool_definitions()} | {"route_to"}
    assert not [t for t in tools if re.search(rf"\b{re.escape(t)}\b", text)], "the layer must not name a tool"
    assert "automated assistant" in text and "Never claim a body" in text           # warmth is no reason to pass as a person
    assert "Never make light of it" in text and "never promise how it will turn out" in text        # care is not false comfort
    assert "Caring is not advice" in text                                            # warmth must not become medical advice (measured on the real model)
    assert "Speak first, then act" in text and "even where a step says to call a tool first" in text    # no silent tool call to a frightened caller
    assert "that comes first" in text                                                # the agent's own rules win


# --- on a call ----------------------------------------------------------------------------------------------------------------

def single(prompt, tools=None, docs=None):
    p = GeminiLiveProcessor(system_instruction=prompt, tool_executor=ToolExecutor(), tool_names=tools, kb_docs=docs)
    decls = p._tool_declarations()
    return p._single_instruction(decls, any(d["name"] in ("find_doctor", "schedule_appointment") for d in decls))


async def test_a_single_agent_keeps_its_own_instructions_first_and_adds_how_it_sounds():
    out = single("You take bookings for Lenovo laptop repairs.", tools=[])
    assert out.startswith("You take bookings for Lenovo laptop repairs.")
    assert HUMAN in out and CARE not in out


async def test_a_single_health_agent_is_told_to_care_whatever_made_it_one():
    by_words = single("You answer for a children's clinic. Ask the patient's name.", tools=[])
    by_tool = single("You help callers.", tools=["find_doctor", "schedule_appointment"])
    by_docs = single("You help callers.", tools=[], docs=["Hospital visiting hours.md"])
    for out in (by_words, by_tool, by_docs):
        assert HUMAN in out and CARE in out


async def test_an_agent_that_names_no_tools_is_not_taken_for_health_care_because_of_the_default_ones():
    """Found by looking at the real stored agents: an agent that names no tools is handed the clinic tools by default, and counting those
    made the e-commerce, banking, HR and laptop-repair agents sound like a ward."""
    laptop = single("You are the primary voice agent for Lenovo Laptop Assistant. Assist customers clearly and concisely.", tools=None)
    assert HUMAN in laptop and CARE not in laptop
    from app.gemini_live_processor import GeminiLiveProcessor as P
    assert {"find_doctor", "schedule_appointment"} <= {d["name"] for d in P(system_instruction="x", tool_executor=ToolExecutor(), tool_names=None)._tool_declarations()}
    named = single("You answer calls.", tools=["find_doctor", "schedule_appointment"])         # tools someone chose are another matter
    assert CARE in named
    assert CARE in single("You are the receptionist of a clinic. Ask the patient's name.", tools=None)          # and so are the words


async def test_an_agent_with_tools_keeps_the_instruction_about_using_them():
    out = single("You are the IT service desk.", tools=["create_symphony_ticket"])
    assert "YOU CAN ACT, NOT JUST TALK" in out and out.index("YOU CAN ACT") < out.index(HUMAN)       # the layer comes last


NODES = [
    types.SimpleNamespace(id="start", type="startNode", position_x=0, position_y=0, data_json={"name": "Start"}),
    types.SimpleNamespace(id="front", type="agentNode", position_x=0, position_y=1,
                          data_json={"name": "Front desk", "role": "orchestrator", "system_prompt": "You are the front desk.", "greeting": "Hello."}),
    types.SimpleNamespace(id="bill", type="agentNode", position_x=0, position_y=2,
                          data_json={"name": "Billing desk", "role": "specialist", "system_prompt": "You answer billing questions."}),
]
EDGES = [types.SimpleNamespace(source_node_id="start", target_node_id="front", label="", condition=""),
         types.SimpleNamespace(source_node_id="front", target_node_id="bill", label="Billing", condition="The caller asks about a bill."),
         types.SimpleNamespace(source_node_id="bill", target_node_id="front", label="Back", condition="")]


def graph_processor(nodes, edges):
    g = build_graph(nodes, edges)
    return g, GeminiLiveProcessor(system_instruction="", tool_executor=ToolExecutor(), graph=g, entry_id=g.entry_inbound)


async def test_every_desk_of_a_health_agent_cares_even_one_that_never_says_so():
    nodes = [*NODES]
    nodes[1] = types.SimpleNamespace(**{**vars(NODES[1]), "data_json": {**NODES[1].data_json, "system_prompt": "You are the front desk of Al Noor Clinic."}})
    g, proc = graph_processor(nodes, EDGES)
    assert g.care                                                                   # one desk says clinic: the whole agent is health care
    for spec in g.nodes.values():
        assert CARE in proc._node_instruction(spec)                                 # so the billing desk is gentle too, and a handover keeps the tone


async def test_an_agent_for_another_business_is_warm_but_not_clinical():
    g, proc = graph_processor(NODES, EDGES)
    assert not g.care
    text = proc._node_instruction(g.nodes["bill"], handoff_note="### Handover\nYou have just taken over.")
    assert HUMAN in text and CARE not in text
    assert text.index("You answer billing questions.") < text.index(HUMAN) < text.index("### Handover")


async def test_the_real_sanjeevani_agent_is_health_care_on_every_step():
    setup = _load(os.path.join(SCRIPTS, "setup_sanjeevani.py"), "setup_sanjeevani_voice")
    nodes, edges = setup.build_graph_definition([f"SANJEEVANI-KB-{i:02d}_Doc.md" for i in range(1, 9)], "")
    rows = [types.SimpleNamespace(id=n["id"], type=n["type"], position_x=n["position"]["x"], position_y=n["position"]["y"], data_json=n["data"]) for n in nodes]
    erows = [types.SimpleNamespace(source_node_id=e["source"], target_node_id=e["target"], label=e["label"], condition=e["condition"]) for e in edges]
    g, proc = graph_processor(rows, erows)
    assert g.care
    assert all(CARE in proc._node_instruction(s) for s in g.nodes.values())
