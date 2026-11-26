"""End-to-end conversation harness.

Runs the REAL router, LLMs, RAG, guard and database against scripted callers. Only the external
ticketing system (and KYC / background name correction) is replaced with in-memory stand-ins, so no
real ticket is ever created. Needs live LLM keys (GROQ_API_KEY, OPENAI_API_KEY) in the environment.
"""
import asyncio
import json
import os
import re
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import AsyncMock

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def configure_environment() -> str:
    os.environ.setdefault("SUMMIT_ENABLE_UPDATES", "true")
    """Must run before any `app` module is imported."""
    tmp = tempfile.mkdtemp(prefix="e2e_")
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp}/e2e.db"
    os.environ["REQUIRE_FIREBASE_AUTH"] = "false"
    os.environ["EMBEDDING_PROVIDER"] = "none"      # keyword RAG: no model download, deterministic
    os.environ["KYC_ENABLED"] = "false"
    os.environ["LANGFUSE_ENABLED"] = "false"
    if BACKEND not in sys.path:
        sys.path.insert(0, BACKEND)
    return tmp


# ─── stand-in for the external ticketing system ────────────────────────────────

class FakeTicketing:
    def __init__(self):
        self.tickets: List[Dict[str, Any]] = []
        self._n = 321

    async def create(self, issue_description: str, priority: str = "P3-Medium", caller_number: str = "") -> str:
        self._n += 1
        tid = f"INC{self._n:06d}"
        self.tickets.append({"id": tid, "description": issue_description, "priority": priority, "caller": caller_number})
        return (f"Incident Ticket Created Successfully! Your ticket number is {tid}. "
                f"Priority: {priority}. It has been logged and assigned to our IT support team.")

    async def update(self, ticket_id: str, status: str = "OPEN", note: str = "") -> str:
        known = next((t for t in self.tickets if t["id"].lower() == str(ticket_id).lower()), None)
        if not known:
            return f"I could not find a ticket with the number {ticket_id}."
        known.setdefault("notes", []).append(note)
        return f"Ticket {known['id']} has been updated."

    async def status(self, ticket_id: str) -> str:
        known = next((t for t in self.tickets if t["id"].lower() == str(ticket_id).lower()), None)
        if known:
            return (f"Ticket {known['id']} is OPEN with priority {known['priority']}. It is assigned to the IT support team "
                    f"and an engineer will update you shortly.")
        if str(ticket_id).upper() == "INC000321":
            return "Ticket INC000321 is IN PROGRESS, priority P2-High, assigned to engineer Priya Nair. Last update: replacement toner ordered."
        return f"I could not find a ticket with the number {ticket_id}."

    async def listing(self, caller_email: str = "", status_filter: str = "OPEN") -> str:
        if not self.tickets:
            return "You have no open tickets."
        return "Your open tickets: " + "; ".join(f"{t['id']} ({t['priority']}): {t['description'][:60]}" for t in self.tickets)


# ─── transcript model ───────────────────────────────────────────────────────────

@dataclass
class Turn:
    user: str
    reply: str
    tool: Optional[str]
    node: Optional[str]
    ms: int
    guard: Optional[Dict[str, Any]] = None


@dataclass
class CallSession:
    workflow: str
    workflow_id: int
    call_id: int
    call_sid: str
    phone: str
    router: Any
    node_id: Optional[str] = None
    history: List[Dict[str, str]] = field(default_factory=list)
    turns: List[Turn] = field(default_factory=list)


class Harness:
    def __init__(self, pace_seconds: float = 5.0):
        self.pace = pace_seconds
        self.ticketing = FakeTicketing()
        self._patched: List[Any] = []

    async def setup(self) -> None:
        from app import database, models  # noqa: F401
        from app.database import init_db
        from app.tool_executor import ToolExecutor
        from app import kyc_core
        from app.websocket_manager import dashboard_manager

        await init_db()
        await self._load_medical_fixture()

        self._patch(ToolExecutor, "_create_symphony_ticket", self.ticketing.create)
        self._patch(ToolExecutor, "_update_symphony_ticket", self.ticketing.update)
        self._patch(ToolExecutor, "_get_symphony_ticket_status", self.ticketing.status)
        self._patch(ToolExecutor, "_list_symphony_incidents", self.ticketing.listing)

        async def _noop(*a, **k):
            return None

        async def _kyc_off() -> bool:
            return False

        self._patch(ToolExecutor, "warm_ticketing", _noop)
        self._patch(ToolExecutor, "_launch_name_correction", _noop)
        self._patch(kyc_core, "kyc_available", _kyc_off)
        self._patch(dashboard_manager, "broadcast", AsyncMock(return_value=None))

    def _patch(self, obj: Any, name: str, value: Any) -> None:
        self._patched.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def teardown(self) -> None:
        for obj, name, original in reversed(self._patched):
            setattr(obj, name, original)

    async def _load_medical_fixture(self) -> None:
        from sqlalchemy import select
        from app.database import async_session
        from app.models import Workflow, WorkflowNode, WorkflowEdge

        with open(os.path.join(FIXTURES, "medical_workflow.json"), encoding="utf-8") as f:
            fx = json.load(f)
        async with async_session() as db:
            if (await db.execute(select(Workflow).where(Workflow.name == fx["name"]))).scalars().first():
                return
            wf = Workflow(name=fx["name"], description="e2e fixture", is_active=False)
            db.add(wf)
            await db.flush()
            for n in fx["nodes"]:
                db.add(WorkflowNode(id=f"e2e-{n['id']}", workflow_id=wf.id, type=n["type"], position_x=n["x"] or 0,
                                    position_y=n["y"] or 0, data_json=n["data"]))
            for e in fx["edges"]:
                db.add(WorkflowEdge(id=f"e2e-{e['id']}", workflow_id=wf.id, source_node_id=f"e2e-{e['source']}",
                                    target_node_id=f"e2e-{e['target']}", label=e["label"], condition=e["condition"]))
            await db.commit()

    async def new_call(self, workflow_name: str, phone: str = "+919876543210") -> CallSession:
        from sqlalchemy import select
        from app.database import async_session
        from app.models import Workflow, Call
        from app.config import get_config
        from app.ai_services import LLMEngineFactory
        from app.agent_router import AgentRouter
        from app.tool_executor import ToolExecutor
        from app.rag_engine import RAGEngine

        async with async_session() as db:
            wf = (await db.execute(select(Workflow).where(Workflow.name == workflow_name))).scalars().first()
            if wf is None:
                raise RuntimeError(f"workflow {workflow_name!r} not found")
            sid = f"E2E{uuid.uuid4().hex[:10]}"
            call = Call(conversation_id=sid, call_sid=sid, caller_number=phone, called_number="e2e", status="active", source="e2e")
            db.add(call)
            await db.commit()
            await db.refresh(call)
            wf_id, call_id = wf.id, call.id

        cfg = get_config()
        router = AgentRouter(llm=LLMEngineFactory.get_engine(cfg), tool_executor=ToolExecutor(),
                             rag_engine=RAGEngine(), config=cfg)
        await router.warm_up()          # in production this runs while the greeting is playing
        return CallSession(workflow=workflow_name, workflow_id=wf_id, call_id=call_id, call_sid=sid, phone=phone, router=router)

    async def say(self, s: CallSession, text: str) -> Turn:
        t0 = time.perf_counter()
        resp = await s.router.route_and_respond(
            session_id=str(s.call_id), user_message=text, history=s.history, current_node_id=s.node_id,
            workflow_id=s.workflow_id, caller_phone=s.phone, provider="tata", call_id=s.call_id, call_sid=s.call_sid)
        ms = int((time.perf_counter() - t0) * 1000)
        reply = str(resp.get("text") or "")
        s.node_id = resp.get("next_node_id") or s.node_id
        s.history.append({"role": "user", "content": text})
        s.history.append({"role": "assistant", "content": reply})
        turn = Turn(user=text, reply=reply, tool=resp.get("tool_called"), node=resp.get("next_node_id"), ms=ms,
                    guard=resp.get("guard_result"))
        s.turns.append(turn)
        if self.pace:
            await asyncio.sleep(self.pace)      # a person does not answer instantly; also keeps Groq under its per-minute cap
        return turn

    async def run(self, workflow_name: str, caller_turns: List[str], phone: str = "+919876543210") -> CallSession:
        s = await self.new_call(workflow_name, phone)
        for text in caller_turns:
            await self.say(s, text)
        return s


# ─── generic conversation-quality measures ─────────────────────────────────────

EMPATHY = re.compile(r"\b(sorry|apolog|understand|frustrat|that sounds|i can imagine|i know how|bear with|thanks for (letting|reaching)|"
                     r"appreciate|no worries|don't worry|let's get|happy to help|i('m| am) here)\b", re.I)
SOLUTION = re.compile(r"\b(try|check|restart|reboot|power|unplug|clear|reset|open|switch|toggle|reconnect|update|log (out|off|in)|"
                      r"run|cancel|queue|cable|settings|workaround|steps?)\b", re.I)
FOLLOW_UP = re.compile(r"(anything else|else i can|any other|follow up|follow-up|let me know|does that|did that|is there anything)", re.I)


def agent_replies(s: CallSession) -> List[str]:
    return [t.reply for t in s.turns]


def metrics(s: CallSession) -> Dict[str, Any]:
    replies = agent_replies(s)
    ms = sorted(t.ms for t in s.turns)
    return {
        "turns": len(s.turns),
        "empathy_in_first_reply": bool(replies and EMPATHY.search(replies[0])),
        "questions_asked": sum(1 for r in replies if "?" in r),
        "solution_offered": any(SOLUTION.search(r) for r in replies),
        "follow_up_offered": any(FOLLOW_UP.search(r) for r in replies),
        "avg_reply_words": round(sum(len(r.split()) for r in replies) / max(len(replies), 1), 1),
        "p50_ms": ms[len(ms) // 2] if ms else 0,
        "max_ms": ms[-1] if ms else 0,
    }
