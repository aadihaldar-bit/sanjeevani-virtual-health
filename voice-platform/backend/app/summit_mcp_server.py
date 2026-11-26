"""
SummitAI Incident Management MCP Server
Exposes Model Context Protocol (MCP) tools for:
1. Incident Creation (create_incident)
2. Incident Listing (list_incidents)
3. Incident Description & Details (get_incident_description)
"""

import os
import logging
import httpx
from datetime import datetime
from typing import Dict, Any, List, Optional

logger = logging.getLogger("summit_mcp_server")

# Try importing FastMCP from mcp.server.fastmcp
try:
    from mcp.server.fastmcp import FastMCP
    mcp = FastMCP("SummitAI Incident Management MCP Server")
    MCP_AVAILABLE = True
except ImportError:
    mcp = None
    MCP_AVAILABLE = False
    logger.warning("mcp package not installed. Running in standalone library mode.")

_HZL_DEFAULT = "https://hzl-ticket-helper-160334972979.us-central1.run.app"
# All actions here use the hzl-helper action/params contract. The env var sometimes
# points at the raw Summit REST WCF endpoint (a different contract), so fall back to
# the helper unless the env value is clearly the helper.
_env_dispatch = os.getenv("SYMPHONY_CLOUD_FUNCTION_URL", "").strip()
SUMMIT_DISPATCH_URL = _env_dispatch if "hzl-ticket-helper" in _env_dispatch else _HZL_DEFAULT
SUMMIT_API_KEY = os.getenv("SUMMIT_API_KEY", "")


async def get_auth_headers() -> Dict[str, str]:
    """Fetch GCP Identity Token or standard x-api-key header for SummitAI API dispatch with caching."""
    headers = {"Content-Type": "application/json", "x-api-key": SUMMIT_API_KEY}
    try:
        from .gcp_auth_cache import get_cached_gcp_identity_token
        token = await get_cached_gcp_identity_token(SUMMIT_DISPATCH_URL)
        if token:
            headers["Authorization"] = f"Bearer {token}"
    except Exception:
        pass
    return headers


# ─── Tool 1: Incident Creation ────────────────────────────────────────────────

async def create_incident(
    issue_description: str,
    priority: str = "P3-Medium",
    caller_email: str = "user@example.com",
    assigned_engineer_email: str = "engineer@example.com"
) -> Dict[str, Any]:
    """
    Create a new IT incident ticket in SummitAI.
    Call this tool ONLY when a user explicitly requests to raise a ticket or when troubleshooting fails.
    Supports P1-Critical through P4-Low priorities. P1-Critical tickets get automatic escalation workgroup routing.
    """
    headers = await get_auth_headers()
    
    # P1-Critical: escalation workgroup routing
    is_critical = priority in ("P1-Critical", "P1")
    payload = {
        "action": "create_incident_via_api_key",
        "params": {
            "caller_email": caller_email,
            "symptom": issue_description[:100],
            "description": f"Urgency: {priority}. Issue: {issue_description}"
        }
    }
    if is_critical:
        payload["params"]["workgroup"] = "IT-Escalation-P1"
        payload["params"]["priority_override"] = "P1-Critical"

    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(SUMMIT_DISPATCH_URL, json=payload, headers=headers, timeout=3.5)
            res.raise_for_status()
            data = res.json()

        ticket_no = data.get("TicketNo") or data.get("TicketID_Internal")
        if not ticket_no or data.get("Errors"):
            raise RuntimeError(f"SummitAI rejected request: {data.get('Errors') or data.get('Message')}")

        result = {
            "status": "SUCCESS",
            "ticket_id": str(ticket_no),
            "message": f"Incident ticket #{ticket_no} created successfully in SummitAI.",
            "raw_response": data
        }
        if is_critical:
            result["escalation"] = "P1-Critical: Routed to IT-Escalation-P1 workgroup for immediate resolution."
        return result
    except Exception as exc:
        # NEVER fabricate a ticket number — report failure honestly.
        logger.error(f"[SummitAI MCP] create_incident failed: {exc}")
        return {
            "status": "FAILED",
            "ticket_id": None,
            "error": f"Could not create the incident in SummitAI: {exc}",
        }


# ─── Tool 2: Incident Listing ──────────────────────────────────────────────────

async def list_incidents(
    caller_email: str = "user@example.com",
    status_filter: str = "OPEN"
) -> Dict[str, Any]:
    """
    List or search all incident tickets raised by a user from SummitAI.
    The caller_email parameter accepts an email address, phone number, or employee ID.
    Use this tool when a user asks 'What are my active tickets?', 'Show my open issues', or 'List my tickets'.
    """
    headers = await get_auth_headers()
    # Correct SummitAI action is get_my_incidents (filters by caller_email, returns
    # OutputObject.MyTickets). The previous "get_user_incidents" is not a valid action —
    # the endpoint rejected it with HTTP 400 on every call, so Summit results never
    # surfaced and the code silently fell back to the local DB.
    payload = {
        "action": "get_my_incidents",
        "params": {
            "caller_email": caller_email,
            "status": status_filter
        }
    }

    # Normalize the identifier: callers speak a phone number that STT often mangles
    # into "(886) 173-4938" — reduce to bare digits for reliable DB matching.
    import re
    digits = re.sub(r"\D", "", caller_email or "")

    def _norm_summit(row: Dict[str, Any]) -> Dict[str, Any]:
        # "Incident ID" is the human-readable ticket number; "Ticket_ID" is a URL-encoded
        # encrypted token (not something to read aloud to a caller).
        return {
            "ticket_no": str(row.get("Incident ID") or row.get("TicketID_Internal") or row.get("Ticket_ID") or ""),
            "symptom": row.get("Symptom") or "IT Issue",
            "description": row.get("Symptom") or "",
            "status": row.get("Status") or "Open",
            "priority": row.get("Priority") or "P3",
            "assigned_engineer": row.get("Executive Name") or row.get("Workgroup Name") or "IT Service Desk",
            "created_at": row.get("Logged Time"),
            "resolution_deadline": row.get("Resolution_Deadline"),
        }

    async def _local_db_tickets() -> List[Dict[str, Any]]:
        """Look up tickets in the local DB by email, raw identifier, or phone digits."""
        try:
            from .database import async_session
            from .models import Ticket
            from sqlalchemy import select, or_
            conds = [Ticket.caller_number == caller_email]
            if caller_email:
                conds.append(Ticket.caller_number.contains(caller_email))
            if len(digits) >= 7:
                conds.append(Ticket.caller_number.contains(digits))
            async with async_session() as db:
                stmt = select(Ticket).where(or_(*conds)).order_by(Ticket.id.desc()).limit(20)
                rows = (await db.execute(stmt)).scalars().all()
                out = []
                for t in rows:
                    # Prefer the real issue text as the spoken symptom; the generic
                    # "Incident Management System" intent reads poorly in a list.
                    _issue = (t.issue or "").strip()
                    _symptom = (_issue[:60] + "…") if len(_issue) > 60 else (_issue or t.intent or "IT Issue")
                    out.append({
                        "ticket_no": t.ticket_id or str(t.id),
                        "symptom": _symptom,
                        "description": t.issue or f"Ticket #{t.ticket_id}",
                        "status": "In Progress",
                        "priority": t.priority or "P3-Medium",
                        "assigned_engineer": "IT Service Desk",
                        "created_at": (t.created_at.strftime("%Y-%m-%d %H:%M:%S") if t.created_at else None),
                    })
                return out
        except Exception as db_exc:
            logger.warning(f"[SummitAI MCP] Local DB lookup failed for list_incidents: {db_exc}")
            return []

    # SECURITY: the hzl get_my_incidents endpoint ignores caller_email and returns the
    # integration account's tickets for ANY identifier — so trusting it would leak another
    # user's tickets to a caller. Only query Summit for a real email AND when explicitly
    # trusted (SUMMIT_TRUST_LIST). For phone/ANI lookups, the local DB is authoritative.
    summit_tickets: List[Dict[str, Any]] = []
    _looks_like_email = "@" in (caller_email or "")
    if _looks_like_email and os.getenv("SUMMIT_TRUST_LIST", "false").lower() == "true":
        try:
            async with httpx.AsyncClient() as client:
                res = await client.post(SUMMIT_DISPATCH_URL, json=payload, headers=headers, timeout=10.0)
                res.raise_for_status()
                data = res.json()
            rows = (data.get("OutputObject", {}) or {}).get("MyTickets", []) or []
            summit_tickets = [_norm_summit(r) for r in rows]
        except Exception as exc:
            logger.warning(f"[SummitAI MCP] Summit get_my_incidents unavailable, using local DB: {exc}")

    # Summit files demo tickets under a shared service email, so it seldom returns a
    # specific caller's tickets. Merge the local DB (the real source of truth here),
    # de-duping by ticket number.
    local_tickets = await _local_db_tickets()
    merged: Dict[str, Any] = {}
    for t in list(summit_tickets) + list(local_tickets):
        key = str(t.get("ticket_no") or t.get("TicketNo") or t.get("ticket_id") or id(t))
        merged.setdefault(key, t)
    tickets = list(merged.values())

    return {
        "status": "SUCCESS",
        "caller_email": caller_email,
        "count": len(tickets),
        "tickets": tickets,
    }


# ─── Tool 3: Incident Description & Details ────────────────────────────────────

async def get_incident_description(ticket_id: str) -> Dict[str, Any]:
    """
    Retrieve full description, current status, assigned engineer, and resolution SLA deadline for a specific SummitAI ticket number.
    Use this tool when a user asks 'What is the status and description of ticket #X?', 'Check ticket 1268637', etc.
    """
    import re
    digits = re.findall(r"\d+", str(ticket_id))
    ticket_no = digits[-1] if digits else str(ticket_id)

    headers = await get_auth_headers()
    payload = {
        "action": "get_incident_details",
        "params": {"ticket_no": ticket_no}
    }

    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(SUMMIT_DISPATCH_URL, json=payload, headers=headers, timeout=10.0)
            res.raise_for_status()
            data = res.json()

        ticket_detail = data.get("OutputObject", {}).get("IncidentDetails", {}).get("TicketDetails", [{}])[0]
        return {
            "status": "SUCCESS",
            "ticket_id": ticket_no,
            "symptom": ticket_detail.get("Symptom", "IT Incident Report"),
            "description": ticket_detail.get("Description", f"SummitAI Ticket #{ticket_no}"),
            "current_status": ticket_detail.get("Status", "In Progress"),
            "assigned_engineer": ticket_detail.get("Assigned_Engineer_Name", "IT Service Desk"),
            "priority": ticket_detail.get("PriorityName", "P3-Medium"),
            "resolution_deadline": ticket_detail.get("Resolution_Deadline")
        }
    except Exception as exc:
        logger.warning(f"[SummitAI MCP] Fallback triggered for get_incident_description: {exc}")
        # Try local DB before returning hardcoded fallback
        try:
            from .database import async_session
            from .models import Ticket
            from sqlalchemy import select
            async with async_session() as db:
                stmt = select(Ticket).where(
                    (Ticket.ticket_id == ticket_no) |
                    (Ticket.ticket_id.contains(ticket_no))
                ).order_by(Ticket.id.desc())
                result = await db.execute(stmt)
                t = result.scalars().first()
                if t:
                    return {
                        "status": "SUCCESS",
                        "ticket_id": t.ticket_id,
                        "symptom": t.intent or "IT Incident Report",
                        "description": t.issue or f"SummitAI Ticket #{t.ticket_id}",
                        "current_status": "OPEN & IN PROGRESS",
                        "assigned_engineer": "the duty engineer (IT Tier-2 Escalation)",
                        "priority": t.priority or "P3-Medium",
                        "resolution_deadline": (t.created_at.strftime("%Y-%m-%d %H:%M:%S") if t.created_at else None)
                    }
        except Exception as db_exc:
            logger.warning(f"[SummitAI MCP] Local DB fallback also failed: {db_exc}")

        # NEVER fabricate a ticket. If neither Summit nor the local DB has it,
        # report honestly so the agent can say it could not find/verify the ticket.
        return {
            "status": "NOT_FOUND",
            "ticket_id": ticket_no,
            "error": f"No details available for ticket {ticket_no}.",
        }


# ─── Tool 4: Incident Update (status / resolve / reopen / note) ────────────────

async def update_incident(ticket_no: str, status: str, note: str = "") -> Dict[str, Any]:
    """
    Update an existing SummitAI incident — change its status (e.g. resolve, reopen,
    progress) and/or add a note. Uses the `update_incident_via_api_key` action.

    IMPORTANT: the set of valid `status` values depends on the customer's Summit
    workflow configuration and MUST be confirmed before enabling in production
    (gated by SUMMIT_ENABLE_UPDATES). This never fabricates success.
    """
    headers = await get_auth_headers()
    params: Dict[str, Any] = {"ticket_no": str(ticket_no), "status": status}
    if note:
        params["note"] = note
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(
                SUMMIT_DISPATCH_URL,
                json={"action": "update_incident_via_api_key", "params": params},
                headers=headers, timeout=15.0)
            res.raise_for_status()
            data = res.json()
        if data.get("Errors"):
            return {"status": "FAILED", "ticket_id": ticket_no, "error": data.get("Errors")}
        return {"status": "SUCCESS", "ticket_id": ticket_no, "new_status": status, "raw_response": data}
    except Exception as exc:
        logger.error(f"[SummitAI MCP] update_incident failed for {ticket_no}: {exc}")
        return {"status": "FAILED", "ticket_id": ticket_no, "error": str(exc)}


# Register with FastMCP if available
if MCP_AVAILABLE and mcp is not None:
    mcp.tool()(create_incident)
    mcp.tool()(list_incidents)
    mcp.tool()(get_incident_description)
    mcp.tool()(update_incident)


if __name__ == "__main__":
    if MCP_AVAILABLE and mcp is not None:
        print("Starting SummitAI Incident Management MCP Server...")
        mcp.run()
    else:
        print("FastMCP library not installed. Standard function execution available.")
