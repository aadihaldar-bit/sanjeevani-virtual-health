import logging
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, UploadFile, File, Form
from pydantic import BaseModel, Field
from sqlalchemy import delete, update
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
import json
import os
import tempfile

from .database import get_db
from .models import Workflow, WorkflowNode, WorkflowEdge
from .workflow_validation import validate_workflow
from .workflow_runtime import sanitize_settings, node_setting_warnings
from .provider_catalog import build_options
from .access import optional_user
from .provider_health import llm_health, same_key
from .config import get_config

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/workflows", tags=["workflows"])

class NodeSchema(BaseModel):
    id: str
    type: str
    position: dict
    data: dict

class EdgeSchema(BaseModel):
    id: str
    source: str
    target: str
    condition: Optional[str] = None
    label: Optional[str] = None

class AIGenerateRequest(BaseModel):
    prompt: str = Field(max_length=4000)
    template_type: Optional[str] = None

class WorkflowSchema(BaseModel):
    id: Optional[int] = None
    name: str
    description: Optional[str] = None
    is_active: bool = False
    nodes: List[NodeSchema] = []
    edges: List[EdgeSchema] = []
    settings: Optional[dict] = None

async def _custom_llm_options(db: AsyncSession, tenant_id, include_all: bool = False):
    from .external_models import visible_endpoints
    try:
        rows = await visible_endpoints(db, tenant_id, include_all=include_all)
    except Exception as exc:                          # a missing table or database must never break the builder
        logger.warning("custom endpoints unavailable: %s", exc)
        return []
    return [{"id": r.to_dict()["provider_id"], "label": f"{r.name} (custom)", "models": [r.model], "available": True,
             "healthy": r.last_test_ok, "issue": r.last_test_error, "custom": True} for r in rows]


async def _org_config(db: AsyncSession, tenant_id):
    """The platform configuration with this organization's own provider keys applied (so a client's own
    OpenAI key makes OpenAI available to them even when the platform has none)."""
    cfg = get_config()
    if tenant_id is None:
        return cfg
    try:
        from .external_models import apply_org_credentials, load_org_credentials
        return apply_org_credentials(cfg, await load_org_credentials(db, tenant_id))
    except Exception as exc:                          # never break the builder over this
        logger.warning("organization keys unavailable: %s", exc)
        return cfg


@router.get("/options")
async def workflow_options(refresh: bool = False, user=Depends(optional_user), db: AsyncSession = Depends(get_db)):
    """Providers, models, voices, languages and tools the builder can offer (with availability and,
    for language models, a cached live health check)."""
    cfg = get_config()
    org_cfg = await _org_config(db, user.tenant_id if user else None)
    opts = build_options(org_cfg)
    from .gemini_live_processor import gemini_live_workflow_ids, use_gemini_live
    # Which agents' calls run on Gemini Live (one model that hears and speaks directly). For those, the language model,
    # speech recognition and voice engine below are not used; the builder says so instead of implying they are.
    from .gemini_live import EXTENDED_MODEL, THINKING_LEVELS, VOICES
    opts["gemini_live"] = {"for_all": bool(use_gemini_live(None)), "workflow_ids": sorted(gemini_live_workflow_ids()),
                           "default_model": os.getenv("GEMINI_LIVE_MODEL", "models/gemini-3.8-live"),
                           "models": [{"id": "", "label": "Standard (the server default)"},
                                      {"id": EXTENDED_MODEL, "label": "Extended thinking (reasons longer, answers slower)"}],
                           "thinking_levels": list(THINKING_LEVELS),
                           "voices": [{"id": name, "label": f"{name} ({feel.lower()})"} for name, feel in VOICES]}
    opts["llm"] = opts["llm"] + await _custom_llm_options(db, user.tenant_id if user else None, bool(user and user.is_super_admin))
    health = await llm_health(cfg, force=refresh)     # probes the platform's keys
    for p in opts["llm"]:
        if p["id"].startswith("custom:"):
            continue                                  # their own test result is on the endpoint
        h = health.get(p["id"]) if same_key(org_cfg, cfg, p["id"]) else None     # unknown for a key we have not probed
        p["healthy"] = h["ok"] if h else None
        p["issue"] = h.get("issue") if h else None
    return opts

WORKFLOW_NODE_TYPES = ("startNode", "agentNode", "toolNode", "endNode")
DRAW_ANSWER_TOKENS = 3000           # a workflow with a few steps and their instructions is well over the 250 a spoken answer gets


def _workflow_json(text: str) -> Any:
    """The JSON in a model's reply, tolerating code fences and chatter around it."""
    text = (text or "").strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()
    if "{" in text:
        text = text[text.find("{"): text.rfind("}") + 1]
    return json.loads(text)


def _number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _tidy_generated_workflow(parsed: Any) -> Dict[str, Any]:
    """A generated workflow in the shape the builder and a call expect, or ValueError when there is nothing to build an agent from.
    Only agent steps carry instructions: a call takes its persona from the first step that has any, so a prompt on the Start step
    would be taken for the agent. Steps of an unknown kind, and arrows to steps that are not there, are dropped. A drawn agent is
    given no tools at all (tools_explicit): which tools an agent has is for a person to choose in the builder, not for a keyword
    in its name to decide, and a model's guess at a tool's name would not match any tool we have."""
    if not isinstance(parsed, dict):
        raise ValueError("the reply is not a workflow")
    nodes: List[Dict[str, Any]] = []
    for n in parsed.get("nodes") or []:
        if not isinstance(n, dict) or not n.get("id") or n.get("type") not in WORKFLOW_NODE_TYPES:
            continue
        if any(str(n["id"]) == existing["id"] for existing in nodes):
            continue                                        # a step id is used once: a second one would not save
        data = dict(n.get("data") or {})
        if n["type"] != "agentNode":
            data.pop("system_prompt", None)
        else:
            data.update(tools=[], tools_explicit=True)
            data.setdefault("use_rag", True)                  # it may look things up in the company's documents
        at = n.get("position") if isinstance(n.get("position"), dict) else {}
        nodes.append({**n, "id": str(n["id"]), "data": data,
                      "position": {"x": _number(at.get("x"), 100.0 + 300.0 * len(nodes)), "y": _number(at.get("y"), 200.0)}})
    if not any(n["type"] == "agentNode" and str(n["data"].get("system_prompt") or "").strip() for n in nodes):
        raise ValueError("the workflow has no agent with instructions")
    known = {n["id"] for n in nodes}
    edges = [{**e, "id": str(e.get("id") or f"e-{i}"), "source": str(e["source"]), "target": str(e["target"])}
             for i, e in enumerate(parsed.get("edges") or [])
             if isinstance(e, dict) and str(e.get("source")) in known and str(e.get("target")) in known]
    return {**parsed, "nodes": nodes, "edges": edges}


@router.post("/generate-ai")
async def generate_ai_workflow(req: AIGenerateRequest):
    """Draw a voice agent workflow from a plain-English requirement, with the strongest thinking model we can reach."""
    from . import architect_model
    from .architect_api import agent_limits
    from .gemini_live_processor import use_gemini_live

    # Calls on the voice engine run ONE agent (the arrows, conditions and tool steps of a drawing are not followed), so a drawing of
    # several desks would be one agent in disguise. Where the older pipeline answers, the drawing may use several agents.
    shape = ("Draw a Start step, exactly ONE agent step that covers everything the requirement asks for, and an End step. Calls run as a "
             "single agent: do not draw several agents or any tool steps."
             if use_gemini_live(None) else
             "Draw a Start step, one agent step for each desk or specialist the requirement names (joined by arrows, each with a condition "
             "for when the caller moves on), a tool step named \"Transfer to a person\" where a caller may ask for one, and an End step.")
    system_prompt = """You are an expert conversational AI architect for a voice agent platform.
Given a requirement, generate a voice agent workflow in JSON format.
Each agent's system_prompt must carry everything the requirement says about that agent: who it speaks for, what it should ask,
what it can do and what it must never do. Write it as instructions to the agent ("You answer calls for ..."), in the languages the
requirement names, asking one question at a time. Keep the person's own details (names, numbers, rules) exactly as they gave them.

What an agent cannot do. Never tell it to do these as if they worked; write what it does instead (take a message, say the team will
follow up, or tell the caller which number to dial):
__LIMITS__

__SHAPE__

JSON Structure:
{
  "name": "string (name of workflow)",
  "description": "string (brief summary)",
  "nodes": [
    {
      "id": "start-1",
      "type": "startNode",
      "position": {"x": 100, "y": 200},
      "data": {"name": "Start", "label": "Start"}
    },
    {
      "id": "agent-1",
      "type": "agentNode",
      "position": {"x": 400, "y": 150},
      "data": {
        "name": "Descriptive Agent Name",
        "system_prompt": "Clear, direct persona and handling instructions",
        "voice": "Aarohi",
        "llm": "groq",
        "use_rag": true
      }
    },
    {
      "id": "end-1",
      "type": "endNode",
      "position": {"x": 750, "y": 100},
      "data": {}
    }
  ],
  "edges": [
    {
      "id": "e-start-agent1",
      "source": "start-1",
      "target": "agent-1",
      "label": "User connects",
      "condition": "Default entry"
    },
    {
      "id": "e-agent1-end1",
      "source": "agent-1",
      "target": "end-1",
      "label": "Caller has what they need",
      "condition": "Done"
    }
  ]
}

Ensure coordinates x/y are nicely spread out (x increasing left-to-right from 100 to 800, y separated by 120px).
Output ONLY valid JSON, no markdown formatting or commentary.
""".replace("__LIMITS__", agent_limits()).replace("__SHAPE__", shape)

    try:
        raw_response = await architect_model.ask(system_prompt, f"Generate a voice workflow for: {req.prompt}", answer_tokens=DRAW_ANSWER_TOKENS)
        return _tidy_generated_workflow(_workflow_json(raw_response))
    except Exception as e:
        # No canned workflow in its place: one made up from nothing looks like success, and the caller (the builder, or a new
        # agent) knows better what to do with the person's description than a stand-in does.
        logger.error(f"Failed to generate AI workflow: {e}")
        raise HTTPException(status_code=502, detail="Architect could not draw that workflow. Try describing it again.")

@router.get("/")
async def list_workflows(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Workflow)
        .options(selectinload(Workflow.nodes), selectinload(Workflow.edges))
        .order_by(Workflow.id.desc())
    )
    workflows = result.scalars().all()
    return [w.to_dict() for w in workflows]

@router.get("/active")
async def get_active_workflow(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Workflow)
        .where(Workflow.is_active == True)
        .options(selectinload(Workflow.nodes), selectinload(Workflow.edges))
    )
    workflow = result.scalars().first()
    if not workflow:
        return None
    return workflow.to_dict()

@router.get("/{workflow_id}")
async def get_workflow(workflow_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Workflow)
        .where(Workflow.id == workflow_id)
        .options(selectinload(Workflow.nodes), selectinload(Workflow.edges))
    )
    workflow = result.scalars().first()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return workflow.to_dict()

@router.post("/")
async def save_workflow(data: WorkflowSchema, db: AsyncSession = Depends(get_db)):
    errors, warnings = validate_workflow(data.nodes, data.edges)
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors, "warnings": warnings})

    # Only touch saved settings when the client sent them (older clients don't).
    settings_sent = "settings" in data.model_fields_set
    clean_settings: Optional[dict] = None
    if settings_sent:
        existing = await db.get(Workflow, data.id) if data.id else None
        owner = existing.tenant_id if existing else None
        extra = await _custom_llm_options(db, owner)
        clean_settings, setting_warnings = sanitize_settings(await _org_config(db, owner), data.settings, extra_llm=extra)
        warnings = warnings + setting_warnings
    else:
        existing = await db.get(Workflow, data.id) if data.id else None
        owner = existing.tenant_id if existing else None
    warnings = warnings + node_setting_warnings(await _org_config(db, owner), data.nodes, clean_settings)

    if data.id:
        result = await db.execute(select(Workflow).where(Workflow.id == data.id))
        workflow = result.scalars().first()
        if not workflow:
            raise HTTPException(status_code=404, detail="Workflow not found")

        workflow.name = data.name
        workflow.description = data.description
        workflow.is_active = data.is_active
        if settings_sent:
            workflow.settings_json = clean_settings
        # Replace the graph inside this same transaction so a failed save keeps the old one.
        await db.execute(delete(WorkflowEdge).where(WorkflowEdge.workflow_id == workflow.id))
        await db.execute(delete(WorkflowNode).where(WorkflowNode.workflow_id == workflow.id))
    else:
        workflow = Workflow(
            name=data.name,
            description=data.description,
            is_active=data.is_active,
            settings_json=clean_settings if settings_sent else None,
        )
        db.add(workflow)
        await db.flush()

    if data.is_active:
        await db.execute(update(Workflow).where(Workflow.id != workflow.id, Workflow.tenant_id == workflow.tenant_id).values(is_active=False))

    # Insert new nodes and edges
    node_objects = []
    for n in data.nodes:
        node = WorkflowNode(
            id=n.id,
            workflow_id=workflow.id,
            type=n.type,
            position_x=n.position.get("x", 0.0),
            position_y=n.position.get("y", 0.0),
            data_json=n.data
        )
        db.add(node)
        node_objects.append(node)
        
    for e in data.edges:
        edge = WorkflowEdge(
            id=e.id,
            workflow_id=workflow.id,
            source_node_id=e.source,
            target_node_id=e.target,
            condition=e.condition,
            label=e.label
        )
        db.add(edge)
        
    workflow_id = workflow.id
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return {"status": "success", "id": workflow_id, "warnings": warnings}

from .rag_engine import RAGEngine
import os

rag_engine = RAGEngine()

@router.post("/upload-kb")
async def upload_kb(node_id: str = Form(...), file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    content = await file.read()
    print(f"Uploaded KB for node {node_id}: {file.filename} ({len(content)} bytes)")
    
    # Save temporarily to parse securely
    safe_filename = os.path.basename(file.filename or "upload.tmp")
    temp_path = os.path.join(tempfile.gettempdir(), safe_filename)
    with open(temp_path, "wb") as f:
        f.write(content)
        
    await rag_engine.ingest_document(temp_path, f"doc_{node_id}_{file.filename}", file.filename, node_id=node_id)
    os.remove(temp_path)
    
    return {"status": "success", "filename": file.filename}

@router.post("/{workflow_id}/activate")
async def activate_workflow(workflow_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Workflow).where(Workflow.id == workflow_id))
    workflow = result.scalars().first()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    # Deactivate the tenant's other workflows (each tenant has its own active agent)
    res_all = await db.execute(select(Workflow).where(Workflow.tenant_id == workflow.tenant_id))
    for w in res_all.scalars().all():
        w.is_active = (w.id == workflow_id)
    
    await db.commit()
    return {"status": "success", "id": workflow.id, "name": workflow.name}

@router.post("/{workflow_id}/deactivate")
async def deactivate_workflow(workflow_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Workflow).where(Workflow.id == workflow_id))
    workflow = result.scalars().first()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    workflow.is_active = False
    await db.commit()
    return {"status": "success", "id": workflow.id}


@router.delete("/{workflow_id}")
async def delete_workflow(workflow_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Workflow).where(Workflow.id == workflow_id))
    workflow = result.scalars().first()
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    await db.delete(workflow)
    await db.commit()
    return {"status": "success"}
