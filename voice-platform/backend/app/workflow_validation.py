"""Structural validation for workflows saved from the visual builder.

Errors block the save (422). Warnings are returned with a successful save.
"""
from collections import Counter
from typing import Any, List, Tuple


def _label(node: Any) -> str:
    data = node.data or {}
    return str(data.get("name") or node.id)


def validate_workflow(nodes: List[Any], edges: List[Any]) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []

    if not nodes:
        return ["Add at least one node before saving."], warnings

    ids = [n.id for n in nodes]
    for node_id, count in Counter(ids).items():
        if count > 1:
            errors.append(f"Node id '{node_id}' is used {count} times; node ids must be unique.")
    known = set(ids)

    for e in edges:
        for end, role in ((e.source, "source"), (e.target, "target")):
            if end not in known:
                errors.append(f"A connection points to a missing {role} node ('{end}').")

    agents = [n for n in nodes if n.type == "agentNode"]
    if not agents:
        errors.append("Add at least one Agent node; a workflow needs an agent to talk to callers.")
    if sum(1 for n in nodes if n.type == "startNode") > 1:
        errors.append("Only one Start node is allowed.")

    for n in agents:
        if not str((n.data or {}).get("system_prompt") or "").strip():
            warnings.append(f"Agent '{_label(n)}' has no system prompt, so it will use generic behavior.")

    if len(nodes) > 1:
        connected = {e.source for e in edges} | {e.target for e in edges}
        for n in nodes:
            if n.id not in connected:
                warnings.append(f"'{_label(n)}' is not connected to anything.")

    return errors, warnings
