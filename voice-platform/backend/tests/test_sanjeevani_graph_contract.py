"""The real Sanjeevani agent, checked the way a live call would use it: every tool a step is given exists, no prompt sends a step to a
tool it does not have, and every handover goes somewhere."""
import importlib.util
import os
import re
import types

from app.gemini_graph import KNOWLEDGE_TOOL, ROUTE_TOOL, build_graph
from app.tool_executor import ToolExecutor

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "setup_sanjeevani.py")
KB_NAMES = [f"SANJEEVANI-KB-{i:02d}_Doc.md" for i in range(1, 9)]


def _definition():
    spec = importlib.util.spec_from_file_location("setup_sanjeevani_contract", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.build_graph_definition(KB_NAMES, "")


def _graph():
    nodes, edges = _definition()
    rows = [types.SimpleNamespace(id=n["id"], type=n["type"], position_x=n["position"]["x"], position_y=n["position"]["y"], data_json=n["data"]) for n in nodes]
    erows = [types.SimpleNamespace(source_node_id=e["source"], target_node_id=e["target"], label=e["label"], condition=e["condition"]) for e in edges]
    return build_graph(rows, erows)


def test_the_agent_has_a_front_desk_three_desks_and_an_outbound_step():
    g = _graph()
    assert g is not None
    assert g.nodes[g.entry_inbound].name == "Front desk" and g.nodes[g.entry_outbound].name == "Patient care assistant"
    assert {t.name for t in g.targets(g.entry_inbound)} == {"Appointment Desk", "Pharmacy Desk", "Feedback Desk"}
    assert g.targets(g.entry_outbound) == []                                # a call we placed stays with its own step


def test_every_tool_a_step_is_given_exists():
    g = _graph()
    known = {t["name"] for t in ToolExecutor().get_tool_definitions()} | {ROUTE_TOOL, KNOWLEDGE_TOOL}
    for node_id, node in g.nodes.items():
        for tool in g.tool_names(node_id):
            assert tool in known, f"{node.name} is given '{tool}', which does not exist"


def test_no_prompt_tells_a_step_to_call_a_tool_it_does_not_have():
    g = _graph()
    every_tool = {t["name"] for t in ToolExecutor().get_tool_definitions()} | {ROUTE_TOOL}
    for node_id, node in g.nodes.items():
        mine = set(g.tool_names(node_id))
        mentioned = {t for t in every_tool if re.search(rf"\b{re.escape(t)}\b", node.prompt)}
        assert mentioned <= mine, f"{node.name}'s prompt mentions {sorted(mentioned - mine)}, which it cannot call"


def test_every_desk_a_prompt_hands_over_to_is_a_real_connection():
    g = _graph()
    for node_id, node in g.nodes.items():
        named = set(re.findall(r'route_to "([^"]+)"', node.prompt))
        for dest in named:
            assert g.target_named(node_id, dest) is not None, f"{node.name} hands over to '{dest}', which it is not connected to"


def test_each_desk_reads_only_its_own_documents():
    g = _graph()
    docs = {n.name: set(n.kb_docs) for n in g.nodes.values()}
    assert "SANJEEVANI-KB-08_Doc.md" in docs["Appointment Desk"] and "SANJEEVANI-KB-08_Doc.md" not in docs["Pharmacy Desk"]
    assert "SANJEEVANI-KB-06_Doc.md" in docs["Pharmacy Desk"] and "SANJEEVANI-KB-06_Doc.md" not in docs["Feedback Desk"]
    assert "SANJEEVANI-KB-04_Doc.md" in docs["Feedback Desk"]


def test_a_desk_can_always_get_back_to_the_front_desk():
    g = _graph()
    for name in ("Appointment Desk", "Pharmacy Desk", "Feedback Desk"):
        node = next(n for n in g.nodes.values() if n.name == name)
        assert any(t.name == "Front desk" for t in g.targets(node.id)), f"{name} cannot return the caller to the front desk"
