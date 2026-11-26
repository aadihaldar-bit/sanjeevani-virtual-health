"""Architect designs an agent the way a person would: how many desks it needs, what each one asks and reads, how it should sound, and
which reference documents are missing (it writes those, for the person to check). Nothing is saved until the person presses Create.

  POST /api/architect/design     a description -> a design (the desks, the documents, the voice), with the thinking model
  POST /api/architect/document   one planned document -> its text, written by the model
  POST /api/architect/build      the design as the person left it, plus the documents -> the saved agent and its knowledge

The model plans and writes the parts only a model can: the steps of each desk, the plan for the documents, the document text. The
parts that must be right every time are written here, the same for every agent: the front desk, the hand-overs between desks, the rule
never to invent a fact, the emergency and no-advice rules for health care, which tools a step has (none but its documents and the
hand-over). So a design cannot leave out a safety rule, and a model's wrong guess at a tool's name reaches nobody.
"""
import asyncio
import logging
import os
import re
import tempfile
import uuid
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from . import architect_model
from .database import get_db
from .gemini_live import CARING_VOICES, EXTENDED_MODEL, THINKING_LEVELS, VOICES
from .voice_character import is_care

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/architect", tags=["architect"])

FRONT = "Front desk"
MAX_DESKS, MAX_DOCS, MAX_DOCS_PER_DESK = 5, 6, 4
MAX_BRIEF, MAX_PROMPT, MAX_DOCUMENT = 1500, 1800, 14000
DESIGN_TOKENS, DOCUMENT_TOKENS = 6000, 3200
# A web request has one minute (nginx). A design is one request; so is each document, which the console asks for side by side.
DESIGN_ATTEMPT_SECS = float(os.getenv("ARCHITECT_DESIGN_ATTEMPT_SECS", "40"))
DESIGN_TOTAL_SECS = float(os.getenv("ARCHITECT_DESIGN_TOTAL_SECS", "52"))
DOCUMENT_ATTEMPT_SECS = float(os.getenv("ARCHITECT_DOCUMENT_ATTEMPT_SECS", "28"))
DOCUMENT_TOTAL_SECS = float(os.getenv("ARCHITECT_DOCUMENT_TOTAL_SECS", "50"))
DOCUMENT_MODELS = ["gemini-3.8-flash", "gemini-pro-latest"]          # a document is quick to write; the stronger model is the second try

LANGUAGE_CODES = {"english": "en-US", "hindi": "hi-IN", "arabic": "ar-XA", "tamil": "ta-IN", "telugu": "te-IN", "kannada": "kn-IN",
                  "malayalam": "ml-IN", "marathi": "mr-IN", "bengali": "bn-IN", "gujarati": "gu-IN", "punjabi": "pa-IN", "urdu": "ur-IN"}
VOICE_BY_NAME = {name.lower(): name for name, _ in VOICES}

_tool_pattern: Optional[re.Pattern] = None


# --- cleaning what a model (or the console, after the person edited it) sends -----------------------------------------------------

def _text(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value if value is not None else "")).strip()[:limit]


def _body(value: Any, limit: int) -> str:
    """Instructions or a document: line breaks kept, runs of spaces and blank lines not."""
    lines = [re.sub(r"[ \t]+", " ", ln).rstrip() for ln in str(value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()[:limit]


def _slug(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")[:30]


def _flag(value: Any) -> bool:
    return value.strip().lower() in ("true", "yes", "1") if isinstance(value, str) else bool(value)


def _tools_mentioned() -> re.Pattern:
    """A tool's name, as a word. Instructions that name a tool are cut: the platform tells each step which tools it has."""
    global _tool_pattern
    if _tool_pattern is None:
        from .tool_executor import ToolExecutor
        names = {t["name"] for t in ToolExecutor().get_tool_definitions()} | {"route_to", "search_knowledge", "search_company_documents"}
        _tool_pattern = re.compile(r"\b(" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)) + r")\b")
    return _tool_pattern


def _without_tool_talk(text: str) -> str:
    pattern = _tools_mentioned()
    return "\n".join(ln for ln in text.split("\n") if not pattern.search(ln))


def _voice(value: Any, care: bool) -> str:
    return VOICE_BY_NAME.get(str(value or "").strip().lower()) or (CARING_VOICES[0] if care else "Aoede")


# --- the design ------------------------------------------------------------------------------------------------------------------

def tidy_design(raw: Any, existing_docs: List[str], brief: str = "") -> Dict[str, Any]:
    """A design in the shape the console shows and `assemble` builds from, or ValueError when there is nothing to build an agent from.
    It is used on what the model designed and again on what the person sends back after editing, so nothing is trusted: names are
    cut to length, a voice that does not exist becomes a real one, a document or desk that is mentioned but not defined is dropped,
    and instructions never name a tool."""
    if not isinstance(raw, dict):
        raise ValueError("the design is not an object")
    existing = {n.lower(): n for n in existing_docs}
    business = _text(raw.get("business"), 80)

    documents: List[Dict[str, Any]] = []
    for item in raw.get("documents") or []:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name"), 70)
        key = _slug(item.get("key") or name)
        if not name or not key or any(d["key"] == key for d in documents):
            continue
        reuse = existing.get(_text(item.get("existing"), 160).lower())
        documents.append({"key": key, "name": reuse or name, "about": _text(item.get("about"), 220),
                          "topics": [t for t in (_text(x, 80) for x in (item.get("topics") or [])) if t][:8], "existing": reuse})
        if len(documents) == MAX_DOCS:
            break
    doc_keys = {d["key"] for d in documents}
    by_reference = {**{_slug(d["name"]): d["key"] for d in documents}, **{k: k for k in doc_keys}}      # a desk may name a document by its key or its name

    desks: List[Dict[str, Any]] = []
    for item in raw.get("desks") or []:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name"), 60)
        body = _without_tool_talk(_body(item.get("prompt"), MAX_PROMPT))
        key = _slug(item.get("key") or name)
        if not name or not body or not key or name.lower() == FRONT.lower() or any(d["key"] == key or d["name"].lower() == name.lower() for d in desks):
            continue
        desks.append({"key": key, "name": name, "does": _text(item.get("does"), 140),
                      "when": _text(item.get("when"), 180) or _text(item.get("does"), 180) or name, "prompt": body,
                      "documents": [k for k in dict.fromkeys(by_reference.get(_slug(r)) for r in (item.get("documents") or []) if isinstance(r, str)) if k][:MAX_DOCS_PER_DESK],
                      "also": [_slug(k) for k in (item.get("also") or []) if isinstance(k, str)]})
        if len(desks) == MAX_DESKS:
            break
    for desk in desks:                                        # a hand-over goes to a desk that exists, and not to itself
        desk["also"] = [k for k in dict.fromkeys(desk["also"]) if k != desk["key"] and any(d["key"] == k for d in desks)]

    agent = None
    single = raw.get("agent") if isinstance(raw.get("agent"), dict) else None
    if len(desks) >= 2:
        used = {k for d in desks for k in d["documents"]}
        documents = [d for d in documents if d["key"] in used]                # a document nobody reads is not worth writing
    else:
        one = single or (desks[0] if desks else None)
        prompt = _without_tool_talk(_body(one.get("prompt"), MAX_PROMPT)) if one else ""
        if not prompt:
            raise ValueError("the design has no agent with instructions")
        agent = {"name": _text(one.get("name"), 60) or business or "Voice assistant", "prompt": prompt}
        desks = []
    care = _flag(raw.get("care")) or is_care(business, brief, *(d["prompt"] for d in desks), (agent or {}).get("prompt", ""),
                                             *(d["name"] for d in documents))
    languages = [l for l in dict.fromkeys(_text(x, 20).title() for x in (raw.get("languages") or []) if isinstance(x, str)) if l][:4] or ["English"]
    model = EXTENDED_MODEL if raw.get("model") == EXTENDED_MODEL else ""
    thinking = str(raw.get("thinking_level") or "").upper()
    return {
        "business": business or "the company",
        "summary": _text(raw.get("summary"), 240),
        "care": care,
        "emergency_number": re.sub(r"[^0-9+ ]", "", str(raw.get("emergency_number") or ""))[:20].strip(),
        "languages": languages,
        "voice": _voice(raw.get("voice"), care),
        "model": model,
        "thinking_level": thinking if model and thinking in THINKING_LEVELS else "",
        "greeting": _text(raw.get("greeting"), 320) or ("Hello, this is an automated assistant"
                                                         + (f" for {business}" if business else "") + ". How can I help you today?"),
        "agent": agent,
        "desks": desks,
        "documents": documents,
        "notes": [n for n in (_text(x, 160) for x in (raw.get("notes") or [])) if n][:4],
    }


# --- the instructions the platform writes ----------------------------------------------------------------------------------------

def _where(design: Dict[str, Any]) -> str:
    number = design["emergency_number"]
    return f"call {number}" if number else "call their local emergency number"


def _emergency(design: Dict[str, Any]) -> List[str]:
    where = _where(design)
    return [f"EMERGENCIES COME FIRST. If they mention trouble breathing, chest pain, a seizure, fainting, heavy bleeding, a severe allergic reaction, "
            f"a child who is very sleepy or hard to wake, or having taken too much medicine: stop everything, tell them to {where} or go to the "
            f"nearest emergency department now, and offer to stay on the line. If they cannot get through, tell them to ask anyone nearby to call for them "
            f"and to keep trying.",
            "NEVER GIVE MEDICAL ADVICE. You may tell them what your documents say (hours, fees, who to see). Do not diagnose, say a symptom is "
            "normal or nothing to worry about, or say what to take, stop or change. Say that is best answered by the doctor, and offer the next step."]


def _rules(design: Dict[str, Any], doc_tool: Optional[str]) -> str:
    said = f" or what {doc_tool} returns" if doc_tool else ""
    lines = _emergency(design) if design["care"] else [
        f"SAFETY FIRST. If the caller says anyone is hurt, in danger or in an emergency, stop everything else, tell them to {_where(design)} right now, "
        "and offer to stay on the line. Do this at once, before asking for any detail. If they cannot get through, tell them to ask anyone nearby to "
        "call for them and to keep trying, and stay with them. Give no medical instructions."]
    lines += [f"NEVER INVENT a name, time, price, reference number or any other fact. Say only what the caller told you{said}. If you do not have it, "
              "say so and offer to take a message so the team can follow up.",
              "LISTEN FIRST. If the caller asks something in the middle of your steps, answer it first, then carry on from where you were. Never ask "
              "again for something they have already told you, even if they told another desk. If they do not have a detail, say that is fine and move on.",
              "YOU CANNOT see a calendar, book into a system or confirm anything yourself. Collect the details, read them back, and say the team will confirm.",
              "Keep every turn short: one idea, then pause. Ask one question at a time. Say numbers, times and references slowly and clearly.",
              ("LANGUAGE. You speak " + ", ".join(design["languages"][:-1]) + " and " + design["languages"][-1] + ", and switch whenever the caller does.")
              if len(design["languages"]) > 1 else "LANGUAGE. You speak " + design["languages"][0] + "."]
    return "\n\n".join(lines)


HANDING_OVER = ("HANDING OVER. If the caller asks for more than one thing, do the part that is yours first. Hand over only what is left, straight to "
                "the desk that fits (the front desk only when no desk does), and never straight back to the desk that sent them to you. In the "
                "reason say what the caller still needs and what is already done or known (for example: nobody is hurt, the place, the car, the "
                "number, the time), so the next desk does not ask for it again.")


def _reads(doc_names: List[str], tool: str, topics: List[str]) -> str:
    if not doc_names:
        return ""
    about = ", ".join(topics[:5]) or "the business"
    return f"REFERENCE DOCUMENTS. When the caller asks about {about}, call {tool} first and answer only from what it returns.\n\n"


def _front_prompt(design: Dict[str, Any]) -> str:
    desks, business = design["desks"], design["business"]
    jobs = [(d["does"] or d["name"]).rstrip(".") for d in desks]
    can = ", ".join(jobs[:-1]) + f", or {jobs[-1]}"
    routes = "\n".join(f"- {d['when'].rstrip('.')}: {d['name']}" for d in desks)
    multi = len(design["languages"]) > 1
    offered = " or ".join(design["languages"])
    language = (f"Listen for the language they choose and speak it from then on, switching whenever they do. If they speak a language that is not {offered}, "
                f"say kindly, in simple words, that you can help in {offered} and ask which they would like. Remember it: pass it when you hand over."
                if multi else "")
    return f"""You are the front desk of {business}, answering a phone call. You are an automated voice assistant and you say so if asked. You are warm, calm, unhurried and respectful.

YOUR JOB: find out what the caller needs and hand them to the right desk. You do not do the desks' work yourself, and you do not ask for names, dates of birth or phone numbers: the desks do that.

1. OPENING
You start with the opening line you were given. {language}

2. SAY WHAT YOU CAN DO
In one sentence say what you can help with ({can}), then ask what they would like, and listen.

3. HAND OVER
When you know what they need, say ONE short sentence (for example, "Of course, let me connect you to our {desks[0]['name'].lower()}") and call route_to:
{routes}
In "reason" put what they need and everything they have already told you (for example whether anyone is hurt, places, times), so the next desk does not ask again. If it is not clear, ask ONE short question. If they ask for something else, say kindly that you can only help with those things here, and offer to take a message so the team can follow up.

""" + _rules(design, None)


def _desk_prompt(design: Dict[str, Any], desk: Dict[str, Any], doc_names: List[str]) -> str:
    others = [d for d in design["desks"] if d["key"] in desk["also"]]
    also = "".join(f'\nIf the caller also wants help with {d["does"] or d["name"].lower()}, call route_to "{d["name"]}".' for d in others)
    topics = [t for k in desk["documents"] for doc in design["documents"] if doc["key"] == k for t in doc["topics"][:2]]
    return (f"You are the {desk['name']} of {design['business']}. You have just taken over this call from another desk (see Handover and the call so far). "
            f"Do not greet again, and skip any step whose answer the caller has already given; carry on from what the caller wants.\n\n{desk['prompt']}\n\n"
            + _reads(doc_names, "search_knowledge", topics)
            + f'If the caller needs something this desk cannot do, or wants to start again, call route_to "{FRONT}".{also}\n\n'
            + HANDING_OVER + "\n\n"
            + _rules(design, "search_knowledge" if doc_names else None))


def _agent_prompt(design: Dict[str, Any], doc_names: List[str]) -> str:
    agent = design["agent"]
    topics = [t for doc in design["documents"] for t in doc["topics"][:2]]
    return (f"You are the voice assistant of {design['business']}, answering a phone call. You are an automated voice assistant and you say so "
            f"if asked.\n\n{agent['prompt']}\n\n"
            + _reads(doc_names, "search_company_documents", topics)
            + _rules(design, "search_company_documents" if doc_names else None))


def assemble(design: Dict[str, Any], doc_files: Dict[str, str]) -> Dict[str, Any]:
    """The steps and lines of the agent, and its settings, from a tidy design. `doc_files` says what each planned document is called
    in the company's knowledge. A design with desks becomes a front desk that hands the caller to them (the shape of the Sanjeevani agent);
    a design with one agent becomes one step."""
    def agent_node(id_, name, role, prompt, y, docs, greeting="", rag=None):
        data = {"name": name, "system_prompt": prompt, "voice": design["voice"], "llm": "", "use_rag": bool(docs) if rag is None else rag,
                "kb_docs": docs, "tools": [], "tools_explicit": True, "greeting": greeting}
        if role:
            data["role"] = role
        return {"id": id_, "type": "agentNode", "position": {"x": 440, "y": y}, "data": data}

    def edge(src, dst, label, condition=""):
        return {"id": f"e-{src}-{dst}", "source": src, "target": dst, "label": label, "condition": condition}

    start = {"id": "start", "type": "startNode", "position": {"x": 80, "y": 300}, "data": {"name": "Start"}}
    if design["desks"]:
        nodes = [start, agent_node("front", FRONT, "orchestrator", _front_prompt(design), 40, [], design["greeting"], rag=False)]
        edges = [edge("start", "front", "A caller phones us")]
        for i, desk in enumerate(design["desks"]):
            names = [doc_files[k] for k in desk["documents"] if k in doc_files]
            nodes.append(agent_node(f"desk-{desk['key']}", desk["name"], "specialist", _desk_prompt(design, desk, names), 200 + 160 * i, names))
            edges.append(edge("front", f"desk-{desk['key']}", desk["name"], desk["when"]))
            edges.append(edge(f"desk-{desk['key']}", "front", "Back to the front desk", "The caller needs something this desk cannot do, or wants to start again."))
            edges += [edge(f"desk-{desk['key']}", f"desk-{other['key']}", other["name"], f"The caller also wants help with {other['does'] or other['name'].lower()}.")
                      for other in design["desks"] if other["key"] in desk["also"]]
        nodes.append({"id": "end", "type": "endNode", "position": {"x": 860, "y": 200 + 160 * len(design["desks"])}, "data": {}})
        edges.append(edge("front", "end", "Call finished", "The caller has no more questions."))
    else:
        names = [doc_files[d["key"]] for d in design["documents"] if d["key"] in doc_files]
        node = agent_node("agent", design["agent"]["name"], "", _agent_prompt(design, names), 200, names, design["greeting"], rag=True)
        nodes = [start, node, {"id": "end", "type": "endNode", "position": {"x": 860, "y": 200}, "data": {}}]
        edges = [edge("start", "agent", "Caller connected"), edge("agent", "end", "Caller has what they need", "The caller has no more questions.")]

    live: Dict[str, Any] = {}
    if design["care"]:
        live.update(turn_end="patient", turn_start="sensitive")           # people who are unwell give dates and numbers slowly
    if design["model"]:
        live.update(model=design["model"], thinking_level=design["thinking_level"] or "LOW")
    codes = [LANGUAGE_CODES[l.lower()] for l in design["languages"] if l.lower() in LANGUAGE_CODES]
    if len(codes) > 1 or (codes and codes[0] != "en-US"):
        live["transcript_languages"] = codes
    return {"nodes": nodes, "edges": edges, "settings": {"gemini_live": live} if live else {}}


# --- the questions put to the model ----------------------------------------------------------------------------------------------

DESK_SHAPE = """{"key": "appointments", "name": "Appointment desk", "does": "booking a visit with a doctor", "when": "The caller wants to book, change or ask about an appointment.", "prompt": "1. ...\\n2. ...", "documents": ["doctors", "faq"], "also": ["billing"]}"""
DOC_SHAPE = """{"key": "doctors", "name": "Doctors and timings", "about": "who the doctors are and when they see patients", "topics": ["cardiology", "paediatrics", "opening hours"], "existing": null}"""


def design_prompt(existing: List[Dict[str, str]]) -> str:
    from .architect_api import agent_limits
    docs = "\n".join(f'  - "{d["name"]}"' + (f' ({d["title"]})' if d.get("title") else "") + (f': {d["snippet"]}' if d.get("snippet") else "")
                     for d in existing) or "  (none yet)"
    voices = "\n".join(f"  - {name}: {feel}" for name, feel in VOICES)
    return f"""You are Architect, a senior conversation designer for a phone voice-agent platform. A person describes the agent they want. Design it the way an expert would; the platform builds exactly what you design.

HOW CALLS WORK
- A call is a live phone conversation by voice. The agent speaks and listens; nothing is typed or shown on a screen.
- One agent can answer everything. Or a FRONT DESK finds out what the caller needs and hands them to a DESK that does one job, with its own instructions and its own reference documents. The platform adds the front desk when there are desks, so you design only the desks.
- A desk can look things up in its reference documents during the call and can hand the caller to another desk. It has no other abilities.
- The platform adds to everything you write: how the agent sounds (warm, human, honest that it is automated), the rule never to invent a fact, how to use its documents, how to hand over, and for health care the emergency and no-medical-advice rules. Do not repeat them. Never name a tool.

WHAT AN AGENT CANNOT DO. Never write these into instructions as if they worked; write what the agent does instead (take the details, say the team will follow up):
{agent_limits()}

WHEN TO USE DESKS
Use 2 to {MAX_DESKS} desks only when the person names, or plainly needs, two or more different jobs (booking, billing, pharmacy, feedback ...) that need different questions or different documents. One job, or several jobs that share the same knowledge, is ONE agent: put its instructions in "agent" and leave "desks" empty. More desks is not better.

HOW TO WRITE INSTRUCTIONS
- Write to the agent ("Ask for ...", "Read back ..."), as numbered steps, one step per turn, in the order a good receptionist would take them. 500 to 1000 characters per desk or agent.
- Say what to ask, what to do next, and what this job must never do. Keep the person's own details (names, numbers, hours, rules) exactly as they gave them. Do not invent any.
- Say what the agent does when the caller needs something it cannot do (take their name and number; the team follows up).
- Where callers may be unwell or worried, make the first step an acknowledgement of their problem; the agent never diagnoses and never advises.
- Write in the languages the person asks for.

DOCUMENTS
A document is plain reference text the agent reads during calls: people, places, hours, prices, policies, common questions. Plan the documents a real business would keep (at most {MAX_DOCS}), each on one clear topic, with the topics it must cover. The company has already uploaded these; when one covers a topic, put its exact name in "existing" instead of planning a new one:
{docs}
List on each desk the keys of the documents it reads (at most {MAX_DOCS_PER_DESK}). A document no desk reads is not needed. For a single agent, list the documents it should know.

VOICE. Choose the speaking voice from this list, by name. For callers who may be unwell or worried choose a gentle one (Sulafat, Achernar, Vindemiatrix or Achird):
{voices}

Reply with ONE JSON object, no code fences, in exactly this shape. For desks fill "desks" and set "agent" to null; for one agent set "desks" to [] and fill "agent":
{{"business": "the organisation's name as the person said it", "summary": "one sentence: what this agent does", "care": false, "emergency_number": "", "languages": ["English"], "voice": "Aoede", "greeting": "what the caller hears first: who this is, that it is an automated assistant, and which language they prefer if there are several", "agent": {{"name": "...", "prompt": "1. ...\\n2. ..."}} or null, "desks": [{DESK_SHAPE}], "documents": [{DOC_SHAPE}], "notes": ["up to three short things the person should know, such as what the agent will not do"]}}
"care" is true when callers may be patients or otherwise unwell. "emergency_number" only when the person gave one. "when" is when the front desk sends a caller here: use the person's own words for every situation that belongs to this desk, including the ones that could be mistaken for another desk (for example a car stuck after an accident, if the person said which desk gets it). "also" lists the keys of other desks a caller may need next. "does" is a short phrase for what the desk helps with ("booking a visit with a doctor"). "notes" are only about what the agent will not do on a call (for example that the team confirms bookings); the platform writes the documents itself, so never mention writing documents or anything the person must do."""


def document_prompt(business: str, brief: str, doc: Dict[str, Any], sample: bool) -> str:
    topics = "; ".join(doc.get("topics") or []) or doc.get("about") or doc["name"]
    if sample:
        mode = ("The person wants a demonstration, so fill this document with realistic SAMPLE details (names, hours, fees, policies) that fit the business. "
                "These are made up, and the document must say so: the second line is \"Sample information for a demonstration. It is not real.\" "
                "and the title ends with \"(sample)\".")
    else:
        mode = ("Use only what the person told you about the business. Never make up a name, a time, a price or a policy. Where a specific detail is not known, do not "
                "leave a blank and do not guess: write what the agent should do instead, for example \"Fees depend on the doctor. The agent does not have them: "
                "it takes the caller's name and number and the team confirms the fee.\" Cover how things work (what is needed to book, how to ask for something, "
                "what the team does next) and the facts the person gave.")
    return f"""You write one reference document for a company's phone voice agent. The agent reads from it during calls and answers aloud, so write for the ear.

Business: {business}
What the person told us about it: {brief}

Document: "{doc['name']}"
Purpose: {doc.get('about') or doc['name']}
It must cover: {topics}

{mode}

Format: Markdown. First line "# {doc['name']}". Then short sections, each starting with a "## " heading that names one topic, followed by two to four plain sentences. Question-and-answer sections are good for common questions. No tables, no lists of more than five items, no links, no emojis. 250 to 500 words in all.
Reply with the document only."""


# --- the work, shared by the endpoints below and by Architect's streamed run ------------------------------------------------------------

class BuildError(Exception):
    """An agent could not be saved. `status` is what an endpoint answers; `detail` is for the person."""

    def __init__(self, detail: str, status: int = 500):
        super().__init__(detail)
        self.detail, self.status = detail, status


def _company_documents() -> List[Dict[str, str]]:
    """What this company has already uploaded: name, title and the start of the text, so a design can use a document that fits."""
    from . import knowledge_routes
    from .tenancy import request_scope
    engine = knowledge_routes.rag_engine
    first: Dict[str, str] = {}
    for chunk in getattr(engine, "chunks", []) or []:
        first.setdefault(chunk["doc_id"], re.sub(r"\s+", " ", chunk.get("text", ""))[:160])
    return [{"name": d["name"], "title": d.get("title") or "", "snippet": first.get(d["id"], "")}
            for d in engine.list_documents(tenant_id=request_scope().tenant_id)[:25]]


def _design_json(raw: str) -> Dict[str, Any]:
    from .architect_api import _json_object
    data = _json_object(raw)
    if data is None:
        raise ValueError("the reply is not a design")
    return data


def document_spec(raw: Dict[str, Any]) -> Dict[str, Any]:
    """A planned document as the model is asked to write it: a name, what it is for, the topics it covers."""
    return {"name": _text(raw.get("name"), 70), "about": _text(raw.get("about"), 220),
            "topics": [t for t in (_text(x, 80) for x in (raw.get("topics") or [])) if t][:8]}


async def design_from_brief(cfg, brief: str, sample: bool, existing: List[Dict[str, str]], *, attempt_secs: float = DESIGN_ATTEMPT_SECS,
                            total_secs: float = DESIGN_TOTAL_SECS, on_thought=None) -> Dict[str, Any]:
    """A tidy design of the agent the brief describes. Raises ArchitectModelError when no model can answer, ValueError when the answer is no design."""
    ask = brief.strip() + ("\n\n(They want the documents filled with made-up sample details, for a demonstration.)" if sample else "")
    shown = {"on_thought": on_thought} if on_thought else {}
    raw = await architect_model.ask(design_prompt(existing), f"The agent the person wants:\n{ask}", cfg=cfg, answer_tokens=DESIGN_TOKENS,
                                    attempt_secs=attempt_secs, total_secs=total_secs, **shown)
    return tidy_design(_design_json(raw), [d["name"] for d in existing], brief)


async def write_document_text(cfg, business: str, brief: str, spec: Dict[str, Any], sample: bool, *, attempt_secs: float = DOCUMENT_ATTEMPT_SECS,
                              total_secs: float = DOCUMENT_TOTAL_SECS) -> str:
    """The text of one planned document. Raises ArchitectModelError when no model can write it."""
    text = await architect_model.ask(document_prompt(business, brief, spec, sample), "Write the document.", cfg=cfg, answer_tokens=DOCUMENT_TOKENS,
                                     models=DOCUMENT_MODELS, thinking="LOW", attempt_secs=attempt_secs, total_secs=total_secs, json_output=False)
    return _body(re.sub(r"^```(?:markdown|md)?\s*|\s*```$", "", text.strip()), MAX_DOCUMENT)


async def _save_document(name: str, content: str) -> Tuple[str, str]:
    """Put a written document into the company's knowledge, under a name not used yet. Returns (name, id)."""
    from . import knowledge_routes
    from .tenancy import request_scope, system_tenant_id_cached
    scope = request_scope()
    engine = knowledge_routes.rag_engine
    taken = {d["name"].lower() for d in engine.list_documents(tenant_id=scope.tenant_id)}
    filename = knowledge_routes._safe_name(name if name.lower().endswith(".md") else name + ".md")
    stem, n = filename[:-3], 2
    while filename.lower() in taken:
        filename, n = f"{stem} ({n}).md", n + 1
    fd, path = tempfile.mkstemp(suffix=".md")
    doc_id = str(uuid.uuid4())
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        await engine.ingest_document(path, doc_id, filename, node_id=None,
                                     tenant_id=scope.tenant_id if scope.tenant_id is not None else system_tenant_id_cached())
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return filename, doc_id


_announcing: set = set()


def _announce(files: List[str]) -> None:
    """Tell the open dashboards that documents were added. Not waited for: a slow message bus must not hold up the person's answer."""
    from . import knowledge_routes

    async def tell() -> None:
        for filename in files:
            await knowledge_routes._broadcast({"type": "knowledge_updated", "document": {"name": filename}})

    if files:
        task = asyncio.ensure_future(tell())
        _announcing.add(task)                      # held until it is done, so it is not collected half way
        task.add_done_callback(_announcing.discard)


async def _remove_documents(saved: List[Tuple[str, str]]) -> None:
    from . import knowledge_routes
    from .tenancy import request_scope
    for _, doc_id in saved:
        try:
            await knowledge_routes.rag_engine.delete_document(doc_id, tenant_id=request_scope().tenant_id)
        except Exception:                                          # noqa: BLE001
            logger.warning("[Architect] could not remove %s after a failed save", doc_id)


async def save_documents(named: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """Save written documents, (name, text) each, into the company's knowledge: all of them or none. Returns (file name, id) for each."""
    saved: List[Tuple[str, str]] = []
    try:
        for name, content in named:
            saved.append(await _save_document(name, content))
    except Exception as exc:                                       # noqa: BLE001  one that cannot be saved takes the others back out
        await _remove_documents(saved)
        logger.error("[Architect] could not save the documents: %s", exc, exc_info=True)
        raise BuildError("Could not save the documents. Nothing was saved.")
    _announce([filename for filename, _ in saved])
    return saved


async def build_design(db: AsyncSession, name: str, brief: str, raw_design: Dict[str, Any], written: Dict[str, str], *, on_documents_saved=None) -> Dict[str, Any]:
    """Save a design as it stands: the documents written for it go into the company's knowledge, then the agent that reads them. Nothing is
    left half-made: if the agent cannot be saved, the documents it came with are taken out again. `on_documents_saved(file names)` is told
    when the documents are in, before the agent is saved. Raises BuildError."""
    from . import knowledge_routes
    from .agents_api import _card_of
    from .models import Workflow, WorkflowEdge, WorkflowNode
    from .tenancy import request_scope
    from .workflow_api import _org_config
    from .workflow_runtime import sanitize_settings

    scope = request_scope()
    existing = [d["name"] for d in _company_documents()]
    try:
        design = tidy_design(raw_design, existing, brief)
    except ValueError as exc:
        raise BuildError(f"There is nothing to build: {exc}.", 422)
    # A planned document the person did not keep (or that was never written) is left out, and the desks that read it simply go without.
    design["documents"] = [d for d in design["documents"] if d["existing"] or len(written.get(d["key"], "")) >= 20]
    kept = {d["key"] for d in design["documents"]}
    for desk in design["desks"]:
        desk["documents"] = [k for k in desk["documents"] if k in kept]

    saved: List[Tuple[str, str]] = []
    doc_files: Dict[str, str] = {d["key"]: d["existing"] for d in design["documents"] if d["existing"]}
    try:
        for doc in design["documents"]:
            if not doc["existing"]:
                filename, doc_id = await _save_document(f"{design['business']} - {doc['name']}", written[doc["key"]])
                saved.append((filename, doc_id))
                doc_files[doc["key"]] = filename
        if saved and on_documents_saved:
            await on_documents_saved([{"id": doc_id, "name": filename} for filename, doc_id in saved])
        parts = assemble(design, doc_files)
        cfg = await _org_config(db, scope.tenant_id)
        settings, _ = sanitize_settings(cfg, parts["settings"])
        workflow = Workflow(name=name.strip(), description=(brief.strip() or design["summary"] or None), is_active=False,
                            settings_json=settings or None)
        db.add(workflow)
        await db.flush()
        for n in parts["nodes"]:
            db.add(WorkflowNode(id=f"{n['id']}-{workflow.id}", workflow_id=workflow.id, type=n["type"], position_x=float(n["position"]["x"]),
                                position_y=float(n["position"]["y"]), data_json=n["data"]))
        for e in parts["edges"]:
            db.add(WorkflowEdge(id=f"{e['id']}-{workflow.id}", workflow_id=workflow.id, source_node_id=f"{e['source']}-{workflow.id}",
                                target_node_id=f"{e['target']}-{workflow.id}", condition=e["condition"], label=e["label"]))
        await db.commit()
        card = await _card_of(db, workflow.id)
    except Exception as exc:                                           # noqa: BLE001  whatever went wrong, leave nothing half-made behind
        await db.rollback()
        await _remove_documents(saved)
        logger.error("[Architect] could not build the agent: %s", exc, exc_info=True)
        raise BuildError("Could not create the agent. Nothing was saved. Try again.")
    _announce([filename for filename, _ in saved])
    return {**card, "desks": [d["name"] for d in design["desks"]], "design": design,
            "documents": [{"name": doc_files[d["key"]], "written_by_architect": not d["existing"]} for d in design["documents"]]}


# --- endpoints -----------------------------------------------------------------------------------------------------------------

class DesignRequest(BaseModel):
    brief: str = Field(min_length=3, max_length=MAX_BRIEF)
    sample_data: bool = False


class DocumentRequest(BaseModel):
    business: str = Field(max_length=80)
    brief: str = Field(max_length=MAX_BRIEF)
    document: Dict[str, Any]
    sample_data: bool = False


class DocumentIn(BaseModel):
    key: str = Field(max_length=40)
    content: str = Field(max_length=MAX_DOCUMENT)


class BuildRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    brief: str = Field(default="", max_length=MAX_BRIEF)
    design: Dict[str, Any]
    documents: List[DocumentIn] = []


@router.post("/design")
async def design_agent(req: DesignRequest, db: AsyncSession = Depends(get_db)):
    """Design an agent from a description: its desks (or its one agent), the documents it reads, and its voice. Saves nothing."""
    from .tenancy import request_scope
    from .workflow_api import _org_config
    existing = _company_documents()
    try:
        design = await design_from_brief(await _org_config(db, request_scope().tenant_id), req.brief, req.sample_data, existing)
    except (architect_model.ArchitectModelError, ValueError) as exc:
        logger.warning("[Architect] could not design an agent: %s", exc)
        raise HTTPException(status_code=502, detail="Architect could not design that just now. Try again, or describe it a little differently.")
    return {"design": design, "voices": [{"id": n, "feel": f} for n, f in VOICES], "existing_documents": [d["name"] for d in existing],
            "models": [{"id": "", "label": "Standard"}, {"id": EXTENDED_MODEL, "label": "Extended thinking (reasons longer, answers slower)"}]}


@router.post("/document")
async def write_document(req: DocumentRequest, db: AsyncSession = Depends(get_db)):
    """Write one planned document for the person to read and edit. Saves nothing."""
    from .tenancy import request_scope
    from .workflow_api import _org_config
    spec = document_spec(req.document)
    if not spec["name"]:
        raise HTTPException(status_code=422, detail="The document has no name.")
    try:
        content = await write_document_text(await _org_config(db, request_scope().tenant_id), req.business, req.brief, spec, req.sample_data)
    except architect_model.ArchitectModelError as exc:
        logger.warning("[Architect] could not write %r: %s", spec["name"], exc)
        raise HTTPException(status_code=502, detail="Architect could not write that document just now.")
    return {"key": _slug(req.document.get("key") or spec["name"]), "name": spec["name"], "content": content}


@router.post("/build")
async def build_agent(req: BuildRequest, db: AsyncSession = Depends(get_db)):
    """Save the design as the person left it: write its documents into the company's knowledge, then the agent that reads them."""
    try:
        built = await build_design(db, req.name, req.brief, req.design, {d.key: _body(d.content, MAX_DOCUMENT) for d in req.documents})
    except BuildError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)
    built.pop("design", None)
    return built
