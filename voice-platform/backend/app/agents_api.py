"""
Agents API — Multi-Agent Management, Templates, Activation, and Graph Runtime.
"""
import logging
import re
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .database import get_db, async_session
from .models import Workflow, WorkflowNode, WorkflowEdge, Call
from .websocket_manager import dashboard_manager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/agents", tags=["agents"])

# ─── Pydantic schemas ──────────────────────────────────────────────────────────

class AgentCard(BaseModel):
    id: int
    name: str
    description: Optional[str]
    is_active: bool
    voice_model: str
    voice_name: str
    languages: List[str]
    node_count: int
    edge_count: int
    created_at: Optional[str]
    updated_at: Optional[str]


class CreateAgentPayload(BaseModel):
    name: str
    template: Optional[str] = None
    prompt: Optional[str] = Field(default=None, max_length=4000)
    description: Optional[str] = None


class AgentChatPayload(BaseModel):
    message: str
    current_node_id: Optional[str] = None
    history: Optional[List[Dict[str, str]]] = []


# ─── Multi-Agent Predefined Templates ──────────────────────────────────────────

MULTI_AGENT_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "medical_receptionist": {
        "description": "Medical clinic receptionist. Captures the patient's medical problem and details, matches them to the right doctor from the clinic directory, and books appointments. Returning-patient history is used internally and never read aloud.",
        "languages": ["en-IN", "hi-IN"],
        "nodes": [
            {
                "id": "start-1",
                "type": "startNode",
                "position": {"x": 60, "y": 260},
                "data": {"name": "Call Entry", "label": "Start"}
            },
            {
                "id": "agent-reception",
                "type": "agentNode",
                "position": {"x": 420, "y": 260},
                "data": {
                    "name": "Clinic Receptionist",
                    "domain": "medical",
                    "system_prompt": "You are the warm, professional front-desk receptionist at STL City Care Multi-Specialty Clinic. Greet the caller and ask how you can help. Your job is to understand the patient's medical problem, collect the details you need (name, contact, and the problem), suggest the right doctor from the clinic, and book an appointment when the patient agrees. You are a receptionist, not a doctor — never give medical advice. Clinic facts you may share if asked: open Monday to Saturday 9am-7pm (closed Sundays); located at 12 MG Road, Pune; we accept Star Health, HDFC Ergo, ICICI Lombard and corporate STL insurance, plus UPI, cards and cash; free basement parking; for emergencies tell the caller to dial 108.",
                    "voice": "Aarohi",
                    "llm": "groq",
                    "tools": ["find_doctor", "schedule_appointment"],
                    "use_rag": False
                }
            },
            {
                "id": "tool-schedule",
                "type": "toolNode",
                "position": {"x": 820, "y": 260},
                "data": {
                    "toolName": "Clinic Scheduling",
                    "tools": ["find_doctor", "schedule_appointment"]
                }
            },
            {
                "id": "end-node",
                "type": "endNode",
                "position": {"x": 1120, "y": 260},
                "data": {}
            }
        ],
        "edges": [
            {"id": "e-start-rec", "source": "start-1", "target": "agent-reception", "label": "Caller connected", "condition": "Default entrypoint"},
            {"id": "e-rec-tool", "source": "agent-reception", "target": "tool-schedule", "label": "Find doctor / book appointment", "condition": "Invoke find_doctor or schedule_appointment"},
            {"id": "e-rec-end", "source": "agent-reception", "target": "end-node", "label": "Appointment booked", "condition": "Caller acknowledged appointment"}
        ]
    },
    "incident_management": {
        "description": "IT Incident Management System for Symphony. Voicebot captures incident details, priority (P1-P4), and triggers Symphony Cloud Function to log tickets in real time.",
        "languages": ["en-IN", "hi-IN"],
        "nodes": [
            {
                "id": "start-1",
                "type": "startNode",
                "position": {"x": 60, "y": 260},
                "data": {"name": "Call Entry", "label": "Start"}
            },
            {
                "id": "agent-triage",
                "type": "agentNode",
                "position": {"x": 420, "y": 260},
                "data": {
                    "name": "IT Service Desk Reception",
                    "system_prompt": "You are the IT Service Desk voice agent for the Symphony Incident Management System. Greet the caller warmly. Ask what IT issue, application bug, network failure, or outage they are experiencing.",
                    "voice": "Aarohi",
                    "llm": "groq",
                    "use_rag": True
                }
            },
            {
                "id": "agent-incident",
                "type": "agentNode",
                "position": {"x": 820, "y": 120},
                "data": {
                    "name": "Symphony Incident Specialist",
                    "system_prompt": "You are the Symphony Incident Logging Specialist. Collect the issue description, priority level (P1-Critical, P2-High, P3-Medium, P4-Low), and caller ID or phone. You have the `create_symphony_ticket` tool — call it via function calling to raise the incident ticket in Symphony. Once logged, read back the Symphony Incident Ticket ID and resolution SLA to the caller clearly.",
                    "voice": "Deepak",
                    "llm": "groq",
                    "tools": ["create_symphony_ticket"],
                    "use_rag": True
                }
            },
            {
                "id": "agent-escalation",
                "type": "agentNode",
                "position": {"x": 820, "y": 400},
                "data": {
                    "name": "NOC Escalation Manager",
                    "system_prompt": "You handle severe P1 outages and critical business escalations. Offer immediate transfer to the on-call Duty Incident Commander (+91 97643 67506) or log an emergency P1 ticket.",
                    "voice": "Radhika",
                    "llm": "groq",
                    "tools": ["create_symphony_ticket", "transfer_to_human"],
                    "use_rag": False
                }
            },
            {
                "id": "tool-symphony",
                "type": "toolNode",
                "position": {"x": 1220, "y": 120},
                "data": {
                    "toolName": "Symphony Cloud Function API",
                    "tools": ["create_symphony_ticket"]
                }
            },
            {
                "id": "tool-transfer",
                "type": "toolNode",
                "position": {"x": 1220, "y": 400},
                "data": {
                    "toolName": "Incident Commander Transfer (+91 97643 67506)"
                }
            },
            {
                "id": "end-node",
                "type": "endNode",
                "position": {"x": 1220, "y": 260},
                "data": {}
            }
        ],
        "edges": [
            {"id": "e-start-triage", "source": "start-1", "target": "agent-triage", "label": "Caller connected", "condition": "Default entrypoint"},
            {"id": "e-triage-incident", "source": "agent-triage", "target": "agent-incident", "label": "Report IT issue / Raise ticket", "condition": "Caller wants to log an incident or report an IT problem"},
            {"id": "e-triage-esc", "source": "agent-triage", "target": "agent-escalation", "label": "P1 critical outage / manager escalation", "condition": "Outage or customer requests manager"},
            {"id": "e-incident-tool", "source": "agent-incident", "target": "tool-symphony", "label": "Trigger Cloud Function API", "condition": "Invoke create_symphony_ticket"},
            {"id": "e-esc-transfer", "source": "agent-escalation", "target": "tool-transfer", "label": "Transfer to Commander", "condition": "Live bridge needed"},
            {"id": "e-incident-end", "source": "agent-incident", "target": "end-node", "label": "Ticket created & confirmed", "condition": "Caller acknowledged ticket ID"}
        ]
    },
    "brother_csr": {
        "description": "Multi-agent customer support for Brother International — troubleshooting, warranty verification, service locator, and escalation.",
        "languages": ["en-IN", "hi-IN"],
        "nodes": [
            {
                "id": "start-1",
                "type": "startNode",
                "position": {"x": 60, "y": 280},
                "data": {"name": "Call Entry", "label": "Start"}
            },
            {
                "id": "agent-triage",
                "type": "agentNode",
                "position": {"x": 420, "y": 280},
                "data": {
                    "name": "General Inquiry Agent",
                    "system_prompt": "You are the primary receptionist for Brother International customer support. Greet the customer warmly, understand their printer issue (model, error code, warranty query), and assist or route them to the right specialist.",
                    "voice": "Aarohi",
                    "llm": "groq",
                    "use_rag": True
                }
            },
            {
                "id": "agent-tech",
                "type": "agentNode",
                "position": {"x": 820, "y": 80},
                "data": {
                    "name": "Technical Support Agent",
                    "system_prompt": "You are Brother's Senior Technical Support Specialist. Provide expert troubleshooting for paper jams, printhead errors, toner issues, Wi-Fi setup, and driver installations on Brother DCP/MFC laser and inkjet printers. Keep steps clear and concise.",
                    "voice": "Deepak",
                    "llm": "groq",
                    "use_rag": True
                }
            },
            {
                "id": "agent-warranty",
                "type": "agentNode",
                "position": {"x": 820, "y": 280},
                "data": {
                    "name": "Warranty & AMC Agent",
                    "system_prompt": "You are Brother's Warranty & Service Contracts Specialist. Assist callers with serial number verification, 1-year standard warranty checks, extended warranty packages, and annual maintenance contracts (AMC).",
                    "voice": "Shweta",
                    "llm": "groq",
                    "use_rag": True
                }
            },
            {
                "id": "agent-escalation",
                "type": "agentNode",
                "position": {"x": 820, "y": 480},
                "data": {
                    "name": "Escalation Agent",
                    "system_prompt": "You are a Senior Customer Relations Manager at Brother International. Calm frustrated customers, offer replacement unit requests or express technician visits, and offer to transfer to the branch manager if needed.",
                    "voice": "Radhika",
                    "llm": "groq",
                    "use_rag": False
                }
            },
            {
                "id": "tool-transfer",
                "type": "toolNode",
                "position": {"x": 1220, "y": 480},
                "data": {
                    "toolName": "Transfer Call (+91 97643 67506)"
                }
            },
            {
                "id": "end-node",
                "type": "endNode",
                "position": {"x": 1220, "y": 140},
                "data": {}
            }
        ],
        "edges": [
            {"id": "e-start-triage", "source": "start-1", "target": "agent-triage", "label": "Customer connects", "condition": "Default entrypoint"},
            {"id": "e-triage-tech", "source": "agent-triage", "target": "agent-tech", "label": "Hardware / print quality issue", "condition": "Customer needs technical troubleshooting or driver help"},
            {"id": "e-triage-warranty", "source": "agent-triage", "target": "agent-warranty", "label": "Warranty / AMC / billing", "condition": "Customer asks about warranty status or service contract"},
            {"id": "e-tech-esc", "source": "agent-tech", "target": "agent-escalation", "label": "Unresolved hardware failure", "condition": "Customer is frustrated or issue requires unit replacement"},
            {"id": "e-esc-transfer", "source": "agent-escalation", "target": "tool-transfer", "label": "Customer requests human supervisor", "condition": "Customer demands human agent or manager"},
            {"id": "e-tech-end", "source": "agent-tech", "target": "end-node", "label": "Issue resolved", "condition": "Customer issue solved satisfactorily"},
            {"id": "e-warranty-end", "source": "agent-warranty", "target": "end-node", "label": "Inquiry completed", "condition": "Customer received warranty info"}
        ]
    },
    "electricity_csr": {
        "description": "Multi-agent utility customer service for power outages, billing disputes, meter queries, and emergency line hazards.",
        "languages": ["en-IN", "hi-IN", "mr-IN"],
        "nodes": [
            {
                "id": "start-1",
                "type": "startNode",
                "position": {"x": 60, "y": 240},
                "data": {"name": "Call Entry", "label": "Start"}
            },
            {
                "id": "agent-triage",
                "type": "agentNode",
                "position": {"x": 420, "y": 240},
                "data": {
                    "name": "Customer Care Triage",
                    "system_prompt": "You are the Electricity Distribution Board intake voice agent. Greet callers warmly, determine whether they are calling about power outages, billing, meter reading, or safety hazards.",
                    "voice": "Aarohi",
                    "llm": "groq",
                    "use_rag": True
                }
            },
            {
                "id": "agent-outage",
                "type": "agentNode",
                "position": {"x": 820, "y": 100},
                "data": {
                    "name": "Outage Dispatch Agent",
                    "system_prompt": "You are the Outage Dispatch Specialist. Take the caller's locality, feeder line number, or consumer ID. Provide restoration estimates and log emergency dispatch tickets for blackout areas.",
                    "voice": "Hemraj",
                    "llm": "groq",
                    "use_rag": True
                }
            },
            {
                "id": "agent-billing",
                "type": "agentNode",
                "position": {"x": 820, "y": 380},
                "data": {
                    "name": "Billing & Metering Agent",
                    "system_prompt": "You are the Electricity Billing Specialist. Answer queries about current bill amounts, tariff rates, peak hour charges, online payment links, and meter reading disputes.",
                    "voice": "Radhika",
                    "llm": "groq",
                    "use_rag": True
                }
            },
            {
                "id": "tool-emergency",
                "type": "toolNode",
                "position": {"x": 1220, "y": 100},
                "data": {
                    "toolName": "Emergency Line Crew Dispatch (+91 97643 67506)"
                }
            },
            {
                "id": "end-node",
                "type": "endNode",
                "position": {"x": 1220, "y": 380},
                "data": {}
            }
        ],
        "edges": [
            {"id": "e-start-triage", "source": "start-1", "target": "agent-triage", "label": "Call connected"},
            {"id": "e-triage-outage", "source": "agent-triage", "target": "agent-outage", "label": "Power blackout / outage report"},
            {"id": "e-triage-billing", "source": "agent-triage", "target": "agent-billing", "label": "High bill / payment query"},
            {"id": "e-outage-emergency", "source": "agent-outage", "target": "tool-emergency", "label": "Sparking wire / transformer fire"},
            {"id": "e-billing-end", "source": "agent-billing", "target": "end-node", "label": "Bill explained"}
        ]
    },
    "emi_reminder": {
        "description": "Multi-agent banking recovery workflow for credit cards & loan EMI reminders, payment links, and hardship disputes.",
        "languages": ["en-IN", "hi-IN", "ta-IN"],
        "nodes": [
            {
                "id": "start-1",
                "type": "startNode",
                "position": {"x": 60, "y": 200},
                "data": {"name": "Call Entry"}
            },
            {
                "id": "agent-reminder",
                "type": "agentNode",
                "position": {"x": 420, "y": 200},
                "data": {
                    "name": "EMI Reminder Agent",
                    "system_prompt": "You are a professional banking voice executive calling regarding an overdue EMI installment. Be polite, verify the customer's identity, remind them of the due amount, and ask for payment commitment date.",
                    "voice": "Aarohi",
                    "llm": "groq",
                    "use_rag": False
                }
            },
            {
                "id": "agent-dispute",
                "type": "agentNode",
                "position": {"x": 820, "y": 200},
                "data": {
                    "name": "Hardship & Dispute Agent",
                    "system_prompt": "You handle payment difficulties and loan restructuring. Offer payment extensions, interest waivers, or restructuring plans with empathy.",
                    "voice": "Deepak",
                    "llm": "groq",
                    "use_rag": False
                }
            },
            {
                "id": "tool-transfer",
                "type": "toolNode",
                "position": {"x": 1220, "y": 200},
                "data": {"toolName": "Transfer to Branch Senior (+91 97643 67506)"}
            }
        ],
        "edges": [
            {"id": "e-1", "source": "start-1", "target": "agent-reminder", "label": "Call connected"},
            {"id": "e-2", "source": "agent-reminder", "target": "agent-dispute", "label": "Customer facing financial hardship / dispute"},
            {"id": "e-3", "source": "agent-dispute", "target": "tool-transfer", "label": "Escalate to manager"}
        ]
    }
}

def _simple_agent_template(description: str, agent_name: str, prompt: str, edge_label: str, edge_condition: str,
                           use_rag: bool = False, languages: Optional[List[str]] = None) -> Dict[str, Any]:
    """One agent that talks to the caller, can hand over to a person, and can end the call. Company-neutral:
    {agent_name} is replaced with the name the user gives the agent. It has no tools of its own (tools_explicit): an agent
    that only collects details must not be handed STL's sample clinic or ticket tools because of a word in its name."""
    return {
        "description": description,
        "languages": languages or ["en-IN", "hi-IN"],
        "nodes": [
            {"id": "start-1", "type": "startNode", "position": {"x": 60, "y": 220}, "data": {"name": "Call Entry", "label": "Start"}},
            {"id": "agent-main", "type": "agentNode", "position": {"x": 420, "y": 220},
             "data": {"name": agent_name, "system_prompt": prompt, "voice": "Aarohi", "llm": "groq", "use_rag": use_rag,
                      "tools": [], "tools_explicit": True}},
            {"id": "tool-handover", "type": "toolNode", "position": {"x": 820, "y": 120}, "data": {"toolName": "Transfer to a person"}},
            {"id": "end-node", "type": "endNode", "position": {"x": 820, "y": 320}, "data": {}},
        ],
        "edges": [
            {"id": "e-start-agent", "source": "start-1", "target": "agent-main", "label": "Caller connected", "condition": "Default entrypoint"},
            {"id": "e-agent-human", "source": "agent-main", "target": "tool-handover", "label": "Caller asks for a person", "condition": "Caller asks for a human"},
            {"id": "e-agent-end", "source": "agent-main", "target": "end-node", "label": edge_label, "condition": edge_condition},
        ],
    }


_STYLE = (" Speak warmly and briefly, one or two short sentences at a time, and ask only one question at a time. "
          "Never use lists, markdown or symbols. Never invent facts, prices or policies you were not given; if you do not "
          "know, say so and offer to take a message.")

MULTI_AGENT_TEMPLATES["customer_support"] = _simple_agent_template(
    "Friendly general customer support. Answers questions from your knowledge base and takes a message when it cannot help.",
    "Support Assistant",
    "You are the voice assistant for {agent_name}. Greet the caller, find out what they need, and answer using the company "
    "knowledge base. If the answer is not in the knowledge base, say you are not sure and offer to pass their question to the team, "
    "taking their name and a contact number." + _STYLE,
    "Question answered", "Caller has what they need", use_rag=True)

MULTI_AGENT_TEMPLATES["appointment_booking"] = _simple_agent_template(
    "Collects booking requests: who is calling, what for, and the preferred day and time, then reads them back. Your team confirms the slot.",
    "Booking Assistant",
    "You take appointment requests for {agent_name}. Collect, one question at a time: the caller's full name, a contact number, what the "
    "appointment is for, and their preferred day and time. Read everything back and ask if it is correct. Then tell them the team will "
    "confirm the slot shortly. You cannot see a calendar, so never promise a specific slot is available." + _STYLE,
    "Request recorded", "Caller confirmed the details")

MULTI_AGENT_TEMPLATES["lead_qualification"] = _simple_agent_template(
    "Sales enquiry handling. Finds out what the caller needs, their timeline and budget range, and arranges a call back from sales.",
    "Sales Assistant",
    "You handle sales enquiries for {agent_name}. Find out, one question at a time, what the caller is looking for, roughly when they "
    "need it, and their approximate budget if they are happy to share. Collect their name and the best number and time for a call back. "
    "Summarise in one sentence and tell them a sales representative will call. Never quote prices you were not given." + _STYLE,
    "Enquiry recorded", "Caller agreed to a call back")

MULTI_AGENT_TEMPLATES["feedback_survey"] = _simple_agent_template(
    "A short feedback call: a rating out of five, what went well, and what could be better.",
    "Feedback Assistant",
    "You run a short feedback call for {agent_name}. Thank the caller for their time. Ask for a rating from one to five, then ask what "
    "went well, then what could be better. Keep it to those three questions, thank them, and end the call. Do not argue with or try to "
    "fix their feedback; just listen and acknowledge it." + _STYLE,
    "Feedback collected", "Caller finished the survey")

# What the gallery shows, and who may use each one. Templates that reach STL's own systems or contain STL demo
# content are for STL's own use: a client's agent must never be wired to STL's ticketing or clinic directory.
TEMPLATE_META: Dict[str, Dict[str, Any]] = {
    "customer_support": {"title": "Customer support", "category": "Support", "summary": "Answers questions from your knowledge base and takes a message when it cannot help.", "features": ["Uses your knowledge base", "Hands over to a person"]},
    "appointment_booking": {"title": "Appointment booking", "category": "Bookings", "summary": "Collects name, contact, reason and preferred time, reads them back, and passes them to your team.", "features": ["Collects booking details", "Team confirms the slot"]},
    "lead_qualification": {"title": "Sales enquiries", "category": "Sales", "summary": "Finds out what the caller needs, when and roughly what budget, and arranges a call back.", "features": ["Qualifies the lead", "Arranges a call back"]},
    "feedback_survey": {"title": "Feedback survey", "category": "Sales", "summary": "A short call: a rating out of five, what went well and what could be better.", "features": ["Three short questions"]},
    "electricity_csr": {"title": "Utility customer service", "category": "Support", "summary": "Power cuts, billing questions and meter queries, with specialist agents for each.", "features": ["3 specialist agents", "Hands over to a person"]},
    "emi_reminder": {"title": "Payment reminders", "category": "Finance", "summary": "Polite reminders for overdue instalments, with a second agent for payment difficulties.", "features": ["2 specialist agents", "Hands over to a manager"]},
    "incident_management": {"title": "IT service desk (STL Symphony)", "category": "Support", "summary": "Raises IT incident tickets over the phone in STL's Symphony system.", "features": ["Creates tickets", "Checks ticket status"], "platform_only": True},
    "medical_receptionist": {"title": "Clinic receptionist (STL City Care)", "category": "Healthcare", "summary": "Matches patients to doctors and books appointments, with an identity check. Uses STL's sample clinic.", "features": ["Books appointments", "Identity check"], "platform_only": True},
    "brother_csr": {"title": "Printer support (Brother demo)", "category": "Support", "summary": "STL's demo for Brother International: troubleshooting, warranty and service locator.", "features": ["4 specialist agents"], "platform_only": True},
}

# ─── Helper Functions ──────────────────────────────────────────────────────────

_PHONE_IN_NAME = re.compile(r"\s*\(\+?\d[\d\s-]{6,}\)")


def _personalise(template: Dict[str, Any], agent_name: str, strip_numbers: bool = False) -> Dict[str, Any]:
    """A copy of the template with the agent's name filled in. For a company's agents, phone numbers that belong to STL are
    removed from step labels: the company sets its own handover number in Settings."""
    def fix(value):
        if isinstance(value, str):
            value = value.replace("{agent_name}", agent_name)
            return _PHONE_IN_NAME.sub("", value) if strip_numbers else value
        if isinstance(value, list):
            return [fix(v) for v in value]
        if isinstance(value, dict):
            return {k: fix(v) for k, v in value.items()}
        return value
    return fix(template)


def _workflow_to_card(w: Workflow) -> dict:
    """Convert a Workflow ORM object to an AgentCard dict."""
    agent_nodes = [n for n in w.nodes if n.type == "agentNode"]
    voice_name = "Aarohi"
    if agent_nodes and agent_nodes[0].data_json:
        voice_name = agent_nodes[0].data_json.get("voice", "Aarohi") or "Aarohi"

    name_lower = w.name.lower()
    if "electricity" in name_lower:
        langs = ["en-IN", "hi-IN", "mr-IN"]
    elif "brother" in name_lower:
        langs = ["en-IN", "hi-IN"]
    elif "emi" in name_lower or "banking" in name_lower:
        langs = ["en-IN", "hi-IN", "ta-IN"]
    else:
        langs = ["en-IN"]

    return {
        "id": w.id,
        "name": w.name,
        "description": w.description or "Multi-agent voice workflow.",
        "is_active": w.is_active,
        "tenant_id": w.tenant_id,
        "voice_model": "timbre-v2.5",
        "voice_name": voice_name,
        "languages": langs,
        "node_count": len(w.nodes),
        "edge_count": len(w.edges),
        "created_at": w.created_at.isoformat() if w.created_at else None,
        "updated_at": w.updated_at.isoformat() if w.updated_at else None,
    }


async def _card_of(db: AsyncSession, workflow_id: int) -> dict:
    """The card of a saved workflow. Its steps are loaded first: a card counts them, and a lazy load cannot happen in an async session."""
    result = await db.execute(select(Workflow).where(Workflow.id == workflow_id)
                              .options(selectinload(Workflow.nodes), selectinload(Workflow.edges)))
    return _workflow_to_card(result.scalars().first())


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/templates")
async def list_templates():
    """The ready-made workflows the caller may start from. STL-only demos are left out for a company's own people."""
    from .tenancy import request_scope
    restricted = request_scope().restricted
    out = []
    for key, meta in TEMPLATE_META.items():
        if restricted and meta.get("platform_only"):
            continue
        template = MULTI_AGENT_TEMPLATES.get(key)
        if not template:
            continue
        agents = [n for n in template["nodes"] if n["type"] == "agentNode"]
        out.append({"id": key, "title": meta["title"], "category": meta["category"], "summary": meta["summary"],
                    "features": meta.get("features", []), "agents": len(agents), "steps": len(template["nodes"]),
                    "languages": template.get("languages", ["en-IN"]), "platform_only": bool(meta.get("platform_only"))})
    return {"templates": out}


@router.get("/")
async def list_agents(db: AsyncSession = Depends(get_db)):
    """List all multi-agent workflows."""
    result = await db.execute(
        select(Workflow)
        .options(selectinload(Workflow.nodes), selectinload(Workflow.edges))
        .order_by(Workflow.updated_at.desc())
    )
    workflows = result.scalars().all()
    from .models import PhoneNumber, Tenant
    from .tenancy import request_scope
    names = dict((await db.execute(select(Tenant.id, Tenant.name))).all())          # one agent is live per company, so say whose
    # The numbers each agent answers: a number given an agent reaches it whether or not it is the live one, so say so.
    given = request_scope().apply(select(PhoneNumber.workflow_id, PhoneNumber.number).where(
        PhoneNumber.workflow_id.is_not(None), PhoneNumber.direction != "outbound"), PhoneNumber)
    numbers: Dict[int, List[str]] = {}
    for workflow_id, number in (await db.execute(given.order_by(PhoneNumber.number))).all():
        numbers.setdefault(workflow_id, []).append(number)
    return [{**_workflow_to_card(w), "company": names.get(w.tenant_id), "numbers": numbers.get(w.id, [])} for w in workflows]


@router.post("/")
async def create_agent(payload: CreateAgentPayload, db: AsyncSession = Depends(get_db)):
    """Create a new multi-agent workflow from a template or custom AI prompt."""
    template_key = (payload.template or "").lower().replace(" ", "_")
    template_data = MULTI_AGENT_TEMPLATES.get(template_key)
    from .tenancy import request_scope
    restricted = request_scope().restricted
    if restricted and TEMPLATE_META.get(template_key, {}).get("platform_only"):
        raise HTTPException(status_code=403, detail="That template is for STL's own use. Pick another one or start from scratch.")
    if template_data:
        template_data = _personalise(template_data, payload.name, strip_numbers=restricted)

    # Check if custom AI prompt is provided
    if (template_key in ["custom_ai", "custom", "ai_prompt"] or not template_data) and payload.prompt:
        try:
            from .workflow_api import generate_ai_workflow, AIGenerateRequest
            ai_res = await generate_ai_workflow(AIGenerateRequest(prompt=payload.prompt, template_type=payload.name))
            if restricted:        # a company's workflow never carries STL's phone number in a step label
                ai_res["nodes"] = _personalise({"nodes": ai_res.get("nodes", [])}, payload.name, strip_numbers=True)["nodes"]
            
            description = payload.description or payload.prompt.strip()          # what the person asked for, whole
            workflow = Workflow(
                name=payload.name,
                description=description,
                is_active=False
            )
            db.add(workflow)
            await db.flush()                     # its id, without saving yet: the workflow and its drawn steps are saved together

            for n in ai_res.get("nodes", []):
                node = WorkflowNode(
                    id=f"{n['id']}-{workflow.id}",
                    workflow_id=workflow.id,
                    type=n["type"],
                    position_x=float(n.get("position", {}).get("x", 100.0)),
                    position_y=float(n.get("position", {}).get("y", 100.0)),
                    data_json=n.get("data", {})
                )
                db.add(node)

            for e in ai_res.get("edges", []):
                edge = WorkflowEdge(
                    id=f"{e['id']}-{workflow.id}",
                    workflow_id=workflow.id,
                    source_node_id=f"{e['source']}-{workflow.id}",
                    target_node_id=f"{e['target']}-{workflow.id}",
                    condition=e.get("condition", ""),
                    label=e.get("label", "")
                )
                db.add(edge)

            await db.commit()
            return await _card_of(db, workflow.id)
        except Exception as err:
            await db.rollback()                  # nothing of a half-made agent stays behind to be made again below
            logger.error(f"[AgentsAPI] Failed to generate custom AI agent, using a single step with the description: {err}")

    description = payload.description or (
        template_data["description"] if template_data else
        ((payload.prompt or "").strip() or "Custom Multi-Agent AI Workflow")
    )

    workflow = Workflow(
        name=payload.name,
        description=description,
        is_active=False,
    )
    db.add(workflow)
    await db.commit()
    await db.refresh(workflow)

    # If template matches, build complete multi-agent graph
    if template_data:
        for n in template_data["nodes"]:
            node = WorkflowNode(
                id=f"{n['id']}-{workflow.id}",
                workflow_id=workflow.id,
                type=n["type"],
                position_x=float(n["position"]["x"]),
                position_y=float(n["position"]["y"]),
                data_json=n.get("data", {}),
            )
            db.add(node)

        for e in template_data["edges"]:
            edge = WorkflowEdge(
                id=f"{e['id']}-{workflow.id}",
                workflow_id=workflow.id,
                source_node_id=f"{e['source']}-{workflow.id}",
                target_node_id=f"{e['target']}-{workflow.id}",
                condition=e.get("condition", ""),
                label=e.get("label", ""),
            )
            db.add(edge)
    else:
        # Generic starter multi-agent graph
        start_node = WorkflowNode(
            id=f"start-{workflow.id}",
            workflow_id=workflow.id,
            type="startNode",
            position_x=100.0,
            position_y=150.0,
            data_json={"name": "Start", "label": "Start"},
        )
        agent_node = WorkflowNode(
            id=f"agent-primary-{workflow.id}",
            workflow_id=workflow.id,
            type="agentNode",
            position_x=420.0,
            position_y=120.0,
            data_json={
                "name": payload.name,
                # what the person asked for is the agent's instructions; the stock sentence only stands in when they gave none
                "system_prompt": (payload.prompt or "").strip()
                or f"You are the primary AI voice agent for {payload.name}. Assist customers clearly and concisely.",
                "voice": "Aarohi",
                "llm": "groq",
                "use_rag": True,                     # it may look things up in the company's documents
                "tools": [],
                "tools_explicit": True,              # and has no other tool until a person gives it one in the builder
            },
        )
        tool_node = WorkflowNode(
            id=f"tool-transfer-{workflow.id}",
            workflow_id=workflow.id,
            type="toolNode",
            position_x=780.0,
            position_y=200.0,
            # a company's workflow never carries STL's phone number: it sets its own handover number in Settings
            data_json={"toolName": "Transfer to a person" if restricted else "Transfer Call (+91 97643 67506)"},
        )
        db.add_all([start_node, agent_node, tool_node])

        edge1 = WorkflowEdge(
            id=f"e-start-agent-{workflow.id}",
            workflow_id=workflow.id,
            source_node_id=start_node.id,
            target_node_id=agent_node.id,
            label="Call Connected",
            condition="Default entrypoint",
        )
        edge2 = WorkflowEdge(
            id=f"e-agent-transfer-{workflow.id}",
            workflow_id=workflow.id,
            source_node_id=agent_node.id,
            target_node_id=tool_node.id,
            label="Customer requests human agent",
            condition="Customer demands human supervisor",
        )
        db.add_all([edge1, edge2])

    await db.commit()
    return await _card_of(db, workflow.id)


@router.post("/{agent_id}/activate")
async def activate_agent(agent_id: int, db: AsyncSession = Depends(get_db)):
    """Set a specific workflow agent as active system-wide."""
    target = (await db.execute(select(Workflow).where(Workflow.id == agent_id))).scalars().first()
    if not target:
        raise HTTPException(status_code=404, detail="Agent not found")
    # Deactivate the tenant's other agents (each tenant has its own active agent)
    all_workflows = await db.execute(select(Workflow).where(Workflow.tenant_id == target.tenant_id))
    for w in all_workflows.scalars().all():
        w.is_active = (w.id == agent_id)

    await db.commit()

    result = await db.execute(
        select(Workflow)
        .where(Workflow.id == agent_id)
        .options(selectinload(Workflow.nodes), selectinload(Workflow.edges))
    )
    workflow = result.scalars().first()
    if not workflow:
        raise HTTPException(status_code=404, detail="Agent not found")

    await dashboard_manager.broadcast({
        "type": "workflow_activated",
        "workflow": workflow.to_dict()
    })
    return {"status": "activated", "agent": _workflow_to_card(workflow)}


@router.post("/{agent_id}/deactivate")
async def deactivate_agent(agent_id: int, db: AsyncSession = Depends(get_db)):
    """Take a company's live agent offline. Until another is made live, that company has no agent answering."""
    target = (await db.execute(select(Workflow).where(Workflow.id == agent_id))).scalars().first()
    if not target:
        raise HTTPException(status_code=404, detail="Agent not found")
    target.is_active = False
    await db.commit()
    return {"status": "deactivated", "id": target.id}


@router.post("/{agent_id}/chat")
async def chat_with_agent(agent_id: int, payload: AgentChatPayload, db: AsyncSession = Depends(get_db)):
    """
    Simulates a multi-agent turn directly against the workflow graph.
    Used for testing the multi-agent runtime.
    """
    from .agent_router import AgentRouter
    from .ai_services import LLMEngineFactory
    from .tool_executor import ToolExecutor
    from .rag_engine import RAGEngine

    llm = LLMEngineFactory.get_engine()
    tool_executor = ToolExecutor()
    rag_engine = RAGEngine()
    router_engine = AgentRouter(llm=llm, tool_executor=tool_executor, rag_engine=rag_engine)

    result = await router_engine.route_and_respond(
        session_id=f"test-sim-{agent_id}",
        user_message=payload.message,
        history=payload.history or [],
        current_node_id=payload.current_node_id,
        workflow_id=agent_id
    )
    return result


@router.delete("/{agent_id}")
async def delete_agent(agent_id: int, db: AsyncSession = Depends(get_db)):
    """Delete a workflow agent."""
    result = await db.execute(select(Workflow).where(Workflow.id == agent_id))
    workflow = result.scalars().first()
    if not workflow:
        raise HTTPException(status_code=404, detail="Agent not found")
    await db.delete(workflow)
    await db.commit()
    return {"status": "deleted", "id": agent_id}
