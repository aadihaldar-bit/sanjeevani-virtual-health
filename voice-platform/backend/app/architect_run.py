"""Architect works a request the way a person would: it thinks, writes a checklist, and does the items one at a time while the person watches.

  POST /api/architect/run   the conversation so far -> a stream of events, one JSON object per line (application/x-ndjson)

The model is asked once, as /chat asks it, and its own summaries of its thinking are passed on as they come. What it proposes is then done
here as a checklist, each item shown as it starts and as it ends: an agent described in words is designed, its documents are written and
saved into Knowledge, and the agent is created as a draft (nothing goes live); a document asked for on its own is written and saved. Nothing is
done that /chat would not have offered, and a change to an agent that already exists still waits for the person to confirm it.

The events:
  thinking  {text, step?}      a summary of the model's thinking (step: the checklist item it belongs to; none for the first look)
  reply     {text}             what Architect says
  plan      {steps}            the checklist: [{id, title, detail}]
  insert    {before, steps}    items found out along the way (the documents a design needs)
  step      {id, status, detail?, result?, secs?}     status: running, done, failed or skipped
  action    {action}           something for the person to decide (a change of voice, a page to open) or to read (their documents)
  done      {ok, summary, note, ...}                  the end; `retry` says whether trying again may work
  error     {message}          nothing could be done
  ping      {}                 still working

The stream outlives nginx's minute for an ordinary request because something is sent at least every few seconds. Only the saving at the
end is kept going if the person leaves: it is one step that either completes or leaves nothing behind, so it is never stopped half way.
"""
import asyncio
import json
import logging
import os
import time
from typing import Any, AsyncIterator, Awaitable, Dict, List, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from . import architect_model, database, tenancy
from .architect_api import MAX_WRITTEN, UNAVAILABLE, ChatRequest, Context, load_context, parse_reply
from .architect_design import (BuildError, build_design, design_from_brief, document_spec, save_documents, write_document_text)
from .database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/architect", tags=["architect"])


def _secs(name: str, default: float) -> float:
    try:
        return float(os.getenv(name) or default)
    except ValueError:
        return default


# A streamed run is not held to nginx's minute, so each model gets longer than it does in /chat, /design and /document.
PLAN_TOKENS = 1500
PLAN_ATTEMPT_SECS, PLAN_TOTAL_SECS = _secs("ARCHITECT_RUN_PLAN_ATTEMPT_SECS", 45), _secs("ARCHITECT_RUN_PLAN_TOTAL_SECS", 70)
DESIGN_ATTEMPT_SECS, DESIGN_TOTAL_SECS = _secs("ARCHITECT_RUN_DESIGN_ATTEMPT_SECS", 80), _secs("ARCHITECT_RUN_DESIGN_TOTAL_SECS", 120)
DOCUMENT_ATTEMPT_SECS, DOCUMENT_TOTAL_SECS = _secs("ARCHITECT_RUN_DOCUMENT_ATTEMPT_SECS", 50), _secs("ARCHITECT_RUN_DOCUMENT_TOTAL_SECS", 80)
HEARTBEAT_SECS = 8.0
DRAFT = "Saved as a draft: it takes no calls until you make it live"


def _count(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _names(names: List[str]) -> str:
    quoted = [f"“{n}”" for n in names]
    return quoted[0] if len(quoted) == 1 else ", ".join(quoted[:-1]) + " and " + quoted[-1]


def _step(id_: str, title: str, detail: str = "") -> Dict[str, str]:
    return {"id": id_, "title": title, "detail": detail}


class Run:
    """The events of one run, in order, and where every checklist item stands. `close` ends the stream."""

    def __init__(self) -> None:
        self.queue: "asyncio.Queue[Optional[Dict[str, Any]]]" = asyncio.Queue()
        self.states: Dict[str, str] = {}
        self.ended = False
        self._began: Dict[str, float] = {}

    def emit(self, t: str, **fields: Any) -> None:
        self.queue.put_nowait({"t": t, **fields})

    def close(self) -> None:
        self.queue.put_nowait(None)

    def thought(self, text: str, step: Optional[str] = None) -> None:
        if text.strip():
            self.emit("thinking", text=text.strip()[:4000], **({"step": step} if step else {}))

    def thinker(self, step: Optional[str] = None):
        async def shown(text: str) -> None:
            self.thought(text, step)
        return shown

    def plan(self, steps: List[Dict[str, str]]) -> None:
        self.states = {s["id"]: "pending" for s in steps}
        self.emit("plan", steps=steps)

    def insert(self, before: str, steps: List[Dict[str, str]]) -> None:
        self.states.update({s["id"]: "pending" for s in steps})
        self.emit("insert", before=before, steps=steps)

    def start(self, id_: str, detail: Optional[str] = None) -> None:
        self._began[id_], self.states[id_] = time.monotonic(), "running"
        self.emit("step", id=id_, status="running", **({"detail": detail} if detail else {}))

    def _end(self, id_: str, status: str, detail: Optional[str], result: Any = None) -> None:
        self.states[id_] = status
        fields: Dict[str, Any] = {"id": id_, "status": status}
        if id_ in self._began:
            fields["secs"] = round(time.monotonic() - self._began.pop(id_), 1)
        if detail:
            fields["detail"] = detail
        if result is not None:
            fields["result"] = result
        self.emit("step", **fields)

    def done(self, id_: str, detail: Optional[str] = None, result: Any = None) -> None:
        self._end(id_, "done", detail, result)

    def fail(self, id_: str, detail: str) -> None:
        self._end(id_, "failed", detail)

    def skip(self, id_: str, detail: str) -> None:
        self._end(id_, "skipped", detail)

    def end(self, ok: bool, summary: str = "", note: str = "", **extra: Any) -> None:
        self.ended = True
        self.emit("done", ok=ok, summary=summary, note=note, **extra)


async def _shielded(work: Awaitable[Any]) -> Any:
    """Run `work` to its end even if the person leaves meanwhile: saving is one step that completes or leaves nothing behind."""
    task = asyncio.ensure_future(work)
    task.add_done_callback(lambda t: t.cancelled() or t.exception())          # an answer nobody waits for is not an unhandled error
    return await asyncio.shield(task)


# --- writing documents -------------------------------------------------------------------------------------------------------------

async def _write_documents(run: Run, ctx: Context, business: str, brief: str, sample: bool, documents: List[Dict[str, Any]]) -> Dict[str, str]:
    """Write the documents side by side, each shown on its own checklist item. Returns the text of each one that could be written, by key."""
    written: Dict[str, str] = {}

    async def one(doc: Dict[str, Any]) -> None:
        step = f"doc-{doc['key']}"
        run.start(step)
        try:
            text = await write_document_text(ctx.cfg, business, brief, document_spec(doc), sample, attempt_secs=DOCUMENT_ATTEMPT_SECS,
                                             total_secs=DOCUMENT_TOTAL_SECS)
        except architect_model.ArchitectModelError as exc:
            logger.warning("[Architect] could not write %r: %s", doc["name"], exc)
            run.fail(step, "Could not write this one just now")
            return
        if len(text) < 20:
            run.fail(step, "The model sent back almost nothing")
            return
        written[doc["key"]] = text
        run.done(step, detail=_count(len(text.split()), "word"), result={"kind": "document", "name": doc["name"], "content": text, "sample": sample})

    await asyncio.gather(*(one(d) for d in documents))
    return written


# --- an agent described in words ---------------------------------------------------------------------------------------------------

def _design_line(design: Dict[str, Any]) -> str:
    desks = design["desks"]
    shape = f"Front desk and {_count(len(desks), 'desk')} ({', '.join(d['name'] for d in desks)})" if desks else "One agent"
    docs = len(design["documents"])
    return f"{shape} · {_count(docs, 'document')} · voice {design['voice']}" if docs else f"{shape} · voice {design['voice']}"


def _design_result(design: Dict[str, Any]) -> Dict[str, Any]:
    return {"kind": "design", "summary": design["summary"], "voice": design["voice"], "care": design["care"], "languages": design["languages"],
            "greeting": design["greeting"], "notes": design["notes"],
            "desks": [{"name": d["name"], "when": d["when"], "prompt": d["prompt"]} for d in design["desks"]],
            "agent": design["agent"]["prompt"] if design["agent"] else None,
            "documents": [{"name": d["existing"] or d["name"], "about": d["about"], "existing": bool(d["existing"])} for d in design["documents"]]}


def _agent_summary(name: str, design: Dict[str, Any], saved: bool, failed: List[str], sample: bool) -> str:
    desks = design["desks"]
    shape = f"a front desk and {_count(len(desks), 'desk')} ({', '.join(d['name'] for d in desks)})" if desks else "one agent"
    parts = [f"I created “{name}” with {shape}. It is a draft: nobody can reach it by phone until you make it live."]
    if saved:
        parts.append("The documents hold made-up sample details for a demo, marked as sample. Replace them before real callers phone." if sample else
                     "The documents hold only what you told me. Where I did not know a fee, a name or a time, they say the team will confirm it, so open a document and add the real details.")
    elif design["documents"]:
        parts.append("It reads the documents you already had.")
    if failed:
        parts.append(f"I could not write {_names(failed)} just now, so the agent has nothing to read there and will offer to take a message. "
                     f"Ask me to write {'it' if len(failed) == 1 else 'them'} again.")
    return " ".join(parts)


async def _agent_from_description(run: Run, ctx: Context, action: Dict[str, Any]) -> None:
    name, brief, sample = action["name"], action["prompt"], bool(action.get("sample_data"))
    documents = [d["name"] for d in ctx.documents]
    run.plan([
        _step("look", "Look through your agents and documents", f"{_count(len(ctx.agents), 'agent')} and {_count(len(documents), 'document')} so far"),
        _step("design", "Design the agent", "The desks, how callers are passed between them, the documents they read and the voice"),
        _step("build", f"Create “{name}”", DRAFT),
    ])
    run.start("look")
    run.done("look", result={"kind": "look", "agents": [a["name"] for a in ctx.agents], "documents": documents})

    run.start("design")
    try:
        design = await design_from_brief(ctx.cfg, brief, sample, ctx.documents, attempt_secs=DESIGN_ATTEMPT_SECS, total_secs=DESIGN_TOTAL_SECS,
                                         on_thought=run.thinker("design"))
    except (architect_model.ArchitectModelError, ValueError) as exc:
        logger.warning("[Architect] could not design an agent: %s", exc)
        run.fail("design", "Architect could not design that just now")
        run.end(False, "I could not design that just now, so nothing was created. Try again, or describe it a little differently.",
                retry=True, fallback={"name": name, "prompt": brief})
        return
    run.done("design", detail=_design_line(design), result=_design_result(design))

    to_write = [d for d in design["documents"] if not d["existing"]]
    written: Dict[str, str] = {}
    if to_write:
        run.insert("build", [_step(f"doc-{d['key']}", f"Write “{d['name']}”", d["about"]) for d in to_write]
                   + [_step("save", "Save the documents into Knowledge", "They go into your Knowledge, where you can open and change them")])
        written = await _write_documents(run, ctx, design["business"], brief, sample, to_write)
    failed = [d["name"] for d in to_write if d["key"] not in written]

    state: Dict[str, Any] = {"saved": []}

    async def documents_saved(saved: List[Dict[str, str]]) -> None:
        state["saved"] = saved
        run.done("save", detail=_count(len(saved), "document") + " saved", result={"kind": "saved", "documents": saved})
        run.start("build")

    if to_write and written:
        run.start("save")
    else:
        if to_write:
            run.skip("save", "There was nothing to save")
        run.start("build")
    try:
        async def build() -> Dict[str, Any]:
            async with database.async_session() as db:
                return await build_design(db, name, brief, design, written, on_documents_saved=documents_saved)

        built = await _shielded(build())
    except BuildError as exc:
        saving = run.states.get("save")
        if saving == "running":                                        # the documents could not be saved, so the agent was never started
            run.fail("save", exc.detail)
            run.fail("build", "Not created")
        else:
            if saving == "done":                                       # they were saved, then the agent could not be: they were taken out again
                run.fail("save", "Taken out again, because the agent could not be created")
            run.fail("build", exc.detail)
        run.end(False, exc.detail, retry=True)
        return

    run.done("build", detail="A draft; open it in the builder to see and change anything", result={
        "kind": "agent", "id": built["id"], "name": built["name"], "desks": built["desks"], "voice": design["voice"],
        "documents": built["documents"]})
    kept = [d["name"] for d in built["documents"]]
    run.end(True, _agent_summary(name, design, bool(state["saved"]), failed, sample),
            note=f'Architect created the agent "{name}" (id {built["id"]})'
                 + (f" with the desks {', '.join(built['desks'])}" if built["desks"] else "")
                 + (f" reading {', '.join(kept)}" if kept else "") + ". It is not live.",
            agent_id=built["id"], notes=design["notes"], documents=state["saved"])


# --- an agent from a ready-made template or a blank page --------------------------------------------------------------------------

async def _agent_from_template(run: Run, ctx: Context, action: Dict[str, Any]) -> None:
    from .agents_api import CreateAgentPayload, create_agent
    name, title = action["name"], action.get("template_title")
    where = f"from the “{title}” template" if title else "from a blank page"
    run.plan([_step("build", f"Create “{name}” {where}", DRAFT)])
    run.start("build")

    async def make() -> Dict[str, Any]:
        async with database.async_session() as db:
            return await create_agent(CreateAgentPayload(name=name, template=action["template"], prompt=""), db)

    try:
        card = await _shielded(make())
    except Exception as exc:                                                        # noqa: BLE001  whatever it was, nothing was made
        logger.warning("[Architect] could not create %r: %s", name, exc)
        run.fail("build", "Could not create the agent")
        run.end(False, "I could not create the agent just now. Nothing was made. Try again.", retry=True)
        return
    run.done("build", detail="A draft; open it in the builder to see and change anything",
             result={"kind": "agent", "id": card["id"], "name": card["name"], "desks": [], "documents": []})
    run.end(True, f"I created “{name}” {where}. It is a draft: nobody can reach it by phone until you make it live. Open it in the builder to read what it says and change anything.",
            note=f'Architect created the agent "{name}" (id {card["id"]}) {where}. It is not live.', agent_id=card["id"], documents=[])


# --- documents on their own --------------------------------------------------------------------------------------------------------

MAX_FACTS = 4000          # a message holds up to 2,000 characters: the facts of two whole messages reach the writers, none cut off from the front


def _persons_words(req: ChatRequest) -> str:
    """What the person has told Architect lately, as they said it: the facts a document is written from."""
    return "\n\n".join(m.content for m in req.messages if m.role == "user")[-MAX_FACTS:]


async def _write_knowledge(run: Run, ctx: Context, action: Dict[str, Any], req: ChatRequest) -> None:
    documents = [{**d, "key": str(i + 1)} for i, d in enumerate(action["documents"][:MAX_WRITTEN])]
    business, sample = action.get("business") or "", bool(action.get("sample_data"))
    run.plan([_step("look", "Look through your documents", f"{_count(len(ctx.documents), 'document')} in Knowledge so far")]
             + [_step(f"doc-{d['key']}", f"Write “{d['name']}”", d["about"]) for d in documents]
             + [_step("save", "Save into Knowledge", "They go into your Knowledge, where you can open and change them")])
    run.start("look")
    run.done("look", result={"kind": "look", "agents": [], "documents": [d["name"] for d in ctx.documents]})

    written = await _write_documents(run, ctx, business or "the company", _persons_words(req), sample, documents)
    if not written:
        run.skip("save", "There was nothing to save")
        run.end(False, "I could not write that just now, so nothing was saved. Try again in a moment.", retry=True)
        return
    run.start("save")
    try:
        saved = await _shielded(save_documents([(f"{business} - {d['name']}" if business else d["name"], written[d["key"]])
                                                for d in documents if d["key"] in written]))
    except BuildError as exc:
        run.fail("save", exc.detail)
        run.end(False, exc.detail, retry=True)
        return
    listed = [{"id": doc_id, "name": filename} for filename, doc_id in saved]
    run.done("save", detail=_count(len(listed), "document") + " saved", result={"kind": "saved", "documents": listed})
    failed = [d["name"] for d in documents if d["key"] not in written]
    parts = [f"I wrote and saved {_count(len(listed), 'document')} into Knowledge: {_names([n['name'][:-3] if n['name'].endswith('.md') else n['name'] for n in listed])}."]
    parts.append("They hold made-up sample details for a demo, marked as sample. Replace them before real callers phone." if sample else
                 "They hold only what you told me. Where I did not know a figure or a name, they say the team will confirm it, so open one and add the real details.")
    parts.append("Agents that read all of your documents use them from now on; an agent with a fixed list of documents needs them added in the builder.")
    if failed:
        parts.append(f"I could not write {_names(failed)} just now. Ask me to write {'it' if len(failed) == 1 else 'them'} again.")
    run.end(True, " ".join(parts), note="Architect wrote and saved the documents " + ", ".join(n["name"] for n in listed) + " into Knowledge.", documents=listed)


# --- showing the documents ---------------------------------------------------------------------------------------------------------

async def _documents_with_readers() -> List[Dict[str, Any]]:
    """The company's documents, and the agents that name each one as theirs (an agent that names none reads them all)."""
    from . import knowledge_routes
    from .models import Workflow
    async with database.async_session() as db:
        agents = (await db.execute(select(Workflow).options(selectinload(Workflow.nodes)))).scalars().all()
    readers: Dict[str, List[str]] = {}
    for agent in agents:
        for node in agent.nodes:
            for doc in (node.data_json or {}).get("kb_docs") or []:
                if agent.name not in readers.setdefault(str(doc), []):
                    readers[str(doc)].append(agent.name)
    docs = knowledge_routes.rag_engine.list_documents(tenant_id=tenancy.request_scope().tenant_id)[:100]
    return [{"id": d["id"], "name": d["name"], "title": d.get("title") or "", "sections": d.get("chunks", 0), "size_kb": d.get("size_kb") or 0,
             "uploaded_at": d.get("uploaded_at"), "built_in": d.get("source") == "builtin", "read_by": readers.get(d["name"], [])} for d in docs]


# --- one request ---------------------------------------------------------------------------------------------------------------------

async def _converse(run: Run, ctx: Context, req: ChatRequest) -> None:
    *earlier, last = req.messages
    history = [{"role": m.role, "content": m.content} for m in earlier]
    try:
        raw = await architect_model.ask(ctx.system_prompt, last.content, history, cfg=ctx.cfg, answer_tokens=PLAN_TOKENS,
                                        attempt_secs=PLAN_ATTEMPT_SECS, total_secs=PLAN_TOTAL_SECS, on_thought=run.thinker())
    except Exception as exc:                                                        # noqa: BLE001  the assistant must never break the console
        logger.warning("[Architect] no model could answer: %s", exc)
        run.emit("error", message=UNAVAILABLE)
        return
    proposal = parse_reply(raw, ctx.templates, ctx.agents)
    run.emit("reply", text=proposal["reply"])
    action = proposal["action"]
    kind = (action or {}).get("type")
    if kind == "create_agent" and action["template"] == "custom_ai":
        await _agent_from_description(run, ctx, action)
    elif kind == "create_agent":
        await _agent_from_template(run, ctx, action)
    elif kind == "write_knowledge":
        await _write_knowledge(run, ctx, action, req)
    elif kind == "show_knowledge":
        run.emit("action", action={**action, "documents": await _documents_with_readers()})
    elif action:
        run.emit("action", action=action)
    if not run.ended:
        run.end(True)


async def _execute(run: Run, ctx: Context, req: ChatRequest) -> None:
    began = time.monotonic()
    try:
        await _converse(run, ctx, req)
    except asyncio.CancelledError:
        raise
    except Exception:                                                               # noqa: BLE001
        logger.exception("[Architect] a run broke off")
        for id_, state in list(run.states.items()):
            if state == "running":
                run.fail(id_, "This did not finish")
        run.emit("error", message="Something went wrong on my side. Nothing more was done. Try again.")
    finally:
        logger.info("[Architect] run took %.1fs; steps %s", time.monotonic() - began, run.states)
        run.close()


async def _stream(scope: tenancy.TenantScope, ctx: Context, req: ChatRequest) -> AsyncIterator[bytes]:
    token = tenancy.current_scope.set(scope)             # the work below runs on its own task: it acts for this person, not for the platform
    run = Run()
    task = asyncio.ensure_future(_execute(run, ctx, req))
    getter: Optional["asyncio.Future[Optional[Dict[str, Any]]]"] = None
    try:
        while True:
            getter = getter or asyncio.ensure_future(run.queue.get())
            finished, _ = await asyncio.wait({getter}, timeout=HEARTBEAT_SECS)
            if not finished:
                yield b'{"t": "ping"}\n'
                continue
            event, getter = getter.result(), None
            if event is None:
                return
            yield (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
    finally:
        for pending in (getter, task):                    # the person left: stop asking models; a save that has begun finishes by itself
            if pending is not None and not pending.done():
                pending.cancel()
        try:
            tenancy.current_scope.reset(token)
        except ValueError:
            tenancy.current_scope.set(None)


@router.post("/run")
async def run_request(req: ChatRequest, db: AsyncSession = Depends(get_db)):
    """Work a request while the person watches. See the events at the top of this file."""
    ctx = await load_context(db)
    logger.info("[Architect] run started: %.80s", req.messages[-1].content)
    return StreamingResponse(_stream(tenancy.request_scope(), ctx, req), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})      # no-buffering: nginx passes each line on at once
