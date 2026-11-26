"""An agent's steps as a Gemini Live call sees them: who answers, who it can hand the caller to, and what each one may use.

A workflow saved in the builder is a graph: steps (agentNode) joined by edges. A step may carry a "role":
  orchestrator  answers an incoming call, finds out what the caller wants and hands them to a specialist
  specialist    does one job (book a doctor, order medicine, take feedback) with its own prompt, tools and documents
  outbound      answers a call we placed (it is told why by the call brief)
A workflow with none of these runs as before: one step, one session. With them, the caller is handed from step to step along the
edges, and each step is a session of its own with only its own prompt, tools and documents.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from .gemini_live import VOICES
from .voice_character import is_care

ROLES = ("orchestrator", "specialist", "outbound")
GEMINI_VOICES = {name.lower() for name, _ in VOICES}
ROUTE_TOOL = "route_to"
KNOWLEDGE_TOOL = "search_knowledge"          # a step's own documents (the desks of a graph)
DOCUMENT_TOOL = "search_company_documents"   # the company's documents, for a single agent (or the ones its builder page attaches)


@dataclass
class NodeSpec:
    id: str
    name: str
    role: str
    prompt: str
    greeting: str = ""
    voice: Optional[str] = None
    tools: List[str] = field(default_factory=list)
    kb_docs: List[str] = field(default_factory=list)


@dataclass
class Edge:
    target: str
    label: str = ""
    condition: str = ""


@dataclass
class GeminiGraph:
    nodes: Dict[str, NodeSpec]
    edges: Dict[str, List[Edge]]
    entry_inbound: str
    entry_outbound: str
    care: bool = False          # the agent speaks with people about their health: every step is gentler for it, so a handover does not change the tone

    def targets(self, node_id: str) -> List[NodeSpec]:
        """The steps this one may hand the caller to (agent steps only), in the order of the edges."""
        out, seen = [], set()
        for e in self.edges.get(node_id, []):
            n = self.nodes.get(e.target)
            if n is not None and n.id not in seen:
                out.append(n)
                seen.add(n.id)
        return out

    def target_named(self, node_id: str, name: str) -> Optional[NodeSpec]:
        want = (name or "").strip().lower()
        for n in self.targets(node_id):
            if n.name.lower() == want or n.id.lower() == want:
                return n
        for n in self.targets(node_id):                       # "the appointment desk" for "Appointment Desk"
            if want and (want in n.name.lower() or n.name.lower() in want):
                return n
        return None

    def when_to_route(self, node_id: str, target_id: str) -> str:
        for e in self.edges.get(node_id, []):
            if e.target == target_id:
                return (e.condition or e.label or "").strip()
        return ""

    def tool_names(self, node_id: str) -> List[str]:
        """What this step may call: its own tools, the document search if it has documents, and the hand-over if it has somewhere to hand to."""
        n = self.nodes[node_id]
        names = [t for t in n.tools if t not in (ROUTE_TOOL, KNOWLEDGE_TOOL)]
        if n.kb_docs:
            names.append(KNOWLEDGE_TOOL)
        if self.targets(node_id):
            names.append(ROUTE_TOOL)
        return names

    def route_declaration(self, node_id: str) -> Optional[Dict[str, Any]]:
        """The Gemini function declaration for handing the caller on, listing each place with when to use it."""
        targets = self.targets(node_id)
        if not targets:
            return None
        lines = []
        for t in targets:
            when = self.when_to_route(node_id, t.id)
            lines.append(f"- {t.name}" + (f": {when}" if when else ""))
        return {
            "name": ROUTE_TOOL,
            "description": ("Hand the caller to the right place when you know what they need. Say one short sentence first "
                            "(for example, that you are connecting them), then call this. Places:\n" + "\n".join(lines)),
            "parameters": {"type": "OBJECT", "properties": {
                "destination": {"type": "STRING", "description": "Exactly one of: " + ", ".join(t.name for t in targets)},
                "reason": {"type": "STRING", "description": "What the caller wants, in a few words, so the next desk can carry on without asking again"},
                "language": {"type": "STRING", "description": "The language the caller chose, such as English or Arabic"}},
                "required": ["destination", "reason"]},
        }


def _agent_data(row: Any) -> Dict[str, Any]:
    return getattr(row, "data_json", None) or {}


def build_graph(node_rows: Iterable[Any], edge_rows: Iterable[Any]) -> Optional[GeminiGraph]:
    """The graph of a saved workflow, or None when no step has a role (then the workflow runs the old, single-step way)."""
    nodes: Dict[str, NodeSpec] = {}
    start_targets: List[str] = []
    order: List[str] = []
    rows = list(node_rows)
    for r in sorted(rows, key=lambda x: (getattr(x, "position_y", 0) or 0, getattr(x, "position_x", 0) or 0, str(getattr(x, "id", "")))):
        d = _agent_data(r)
        if getattr(r, "type", "") != "agentNode" or not d.get("system_prompt"):
            continue
        voice = d.get("voice") if str(d.get("voice") or "").lower() in GEMINI_VOICES else None
        nodes[str(r.id)] = NodeSpec(
            id=str(r.id), name=str(d.get("name") or r.id), role=str(d.get("role") or ""), prompt=d["system_prompt"],
            greeting=str(d.get("opening_line") or d.get("greeting") or ""), voice=voice,
            tools=[str(t) for t in (d.get("tools") or [])], kb_docs=[str(x) for x in (d.get("kb_docs") or [])] if d.get("use_rag", True) else [])
        order.append(str(r.id))
    if not any(n.role in ROLES for n in nodes.values()):
        return None
    edges: Dict[str, List[Edge]] = {}
    for e in edge_rows:
        src, dst = str(e.source_node_id), str(e.target_node_id)
        label, cond = getattr(e, "label", "") or "", getattr(e, "condition", "") or ""
        if src not in nodes and any(str(r.id) == src and getattr(r, "type", "") == "startNode" for r in rows):
            if dst in nodes:
                start_targets.append(dst)
            continue
        if src in nodes and dst in nodes:
            edges.setdefault(src, []).append(Edge(dst, label, cond))
    inbound = next((n.id for n in nodes.values() if n.role == "orchestrator"), None) or (start_targets[0] if start_targets else order[0])
    outbound = next((n.id for n in nodes.values() if n.role == "outbound"), None) or inbound
    care = is_care(*(f"{n.name} {n.prompt} {' '.join(n.kb_docs)}" for n in nodes.values()), tools=[t for n in nodes.values() for t in n.tools])
    return GeminiGraph(nodes=nodes, edges=edges, entry_inbound=inbound, entry_outbound=outbound, care=care)
