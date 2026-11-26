"""
Tool Executor Segment
Manages tool definitions, CRM lookups, ticketing, and function calls.
Implements BaseToolExecutor interface.
"""

import os
import re
import inspect
import random
import logging
import asyncio
import httpx
from datetime import datetime
from typing import Callable, Dict, Any, List, Optional

from .interfaces import BaseToolExecutor
from .config import get_config
from .sanjeevani_desk import SanjeevaniDeskMixin
from .sanjeevani_pharmacy import SanjeevaniPharmacyMixin

logger = logging.getLogger(__name__)

# Short-window idempotency guard: maps sha1(caller|issue) -> (ticket_id, epoch_seconds).
# Prevents accidental duplicate ticket creation within a single call.
_RECENT_CREATES: Dict[str, Any] = {}
_IDEMPOTENCY_WINDOW_SECS = 90
_UNCERTAIN_CREATES: Dict[str, float] = {}      # create calls that timed out: Summit may still have made the ticket

# ─── Sanjeevani patient-care: per-call identity gate ──────────────────────────────────────
# Every tool that touches a patient's data refuses until confirm_identity has matched the patient's date of
# birth on THIS call. The result lives on the call record (metadata_json: identity_verified, identity_attempts,
# identity_failed), not in memory, so a restart mid-call cannot reopen or forget the gate.
MAX_IDENTITY_ATTEMPTS = 2
IDENTITY_FIRST = "I need to confirm your identity first before I can share or record anything about the medicines."
IDENTITY_FAILED = ("I'm sorry, I'm not able to confirm your identity on this call, so I can't share any details. "
                   "I'll try again another time. Thank you, goodbye.")

# ─── Speech-friendly formatting helpers (for the medical receptionist voice flow) ──────
# The TTS reads "10:00-13:00" digit-by-digit, which sounds robotic on a call.
# These turn raw schedule data into natural spoken phrases.
_DAY_FULL = {
    "mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday",
    "fri": "Friday", "sat": "Saturday", "sun": "Sunday",
}


def _join_natural(items: List[str]) -> str:
    """['A','B','C'] -> 'A, B and C'."""
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f" and {items[-1]}"


def _spoken_time(hhmm: str) -> str:
    """'13:00' -> '1 PM', '10:30' -> '10:30 AM'."""
    try:
        h_s, m_s = hhmm.split(":")
        h, m = int(h_s), int(m_s)
    except Exception:
        return hhmm
    ampm = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12} {ampm}" if m == 0 else f"{h12}:{m:02d} {ampm}"


def _hm_to_min(hhmm: str):
    try:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    except Exception:
        try:
            return int(hhmm) * 60
        except Exception:
            return None


def _spoken_availability(slots: List[str]) -> str:
    """['Mon 10:00-13:00','Wed 10:00-13:00','Fri 10:00-13:00']
    -> 'on Monday, Wednesday and Friday, from 10 AM to 1 PM'.
    Days that share the same time range are grouped so it reads like a human."""
    groups: Dict[tuple, List[str]] = {}
    order: List[tuple] = []
    for s in slots:
        try:
            day_part, time_part = s.split()
            start, end = time_part.split("-")
        except Exception:
            continue
        key = (start, end)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(_DAY_FULL.get(day_part[:3].lower(), day_part))
    if not groups:
        return "at a few slots this week — let me know a day that suits you"
    phrases = []
    for start, end in order:
        days = _join_natural(groups[(start, end)])
        phrases.append(f"on {days}, from {_spoken_time(start)} to {_spoken_time(end)}")
    return "; and ".join(phrases)


class ToolExecutor(SanjeevaniPharmacyMixin, SanjeevaniDeskMixin, BaseToolExecutor):
    """
    Manages registration and execution of tools (functions) that the AI agent can call.
    Includes Client CRM, Symphony Summit Incident Management (via Cloud Function & REST API), and Neox Helpdesk integrations.
    """

    def __init__(self):
        self._tools: Dict[str, dict] = {}
        self._doctors = self._load_doctors()
        self._register_sanjeevani_desk_tools()      # the appointment and feedback desks (sanjeevani_desk.py)
        self._register_sanjeevani_pharmacy_tools()  # the pharmacy desk (sanjeevani_pharmacy.py)

        # ─── Symphony Summit Incident Management System Tools ──────────────────────────
        self.register_tool(
            name="create_symphony_ticket",
            handler=self._create_symphony_ticket,
            description="Create an IT incident ticket in Symphony Incident Management System. Call this tool ONLY when the user explicitly requests to raise a ticket or when first-line troubleshooting has been attempted and failed. DO NOT call for initial problem diagnosis or when the user asks for help resolving the issue first. issue_description MUST be the summary of the caller's ROOT technical problem.",
            parameters={
                "issue_description": {
                    "type": "string", 
                    "description": "Summary of the caller's actual ROOT IT problem (e.g. 'Laptop not turning on / booting', 'Server database connection error'). DO NOT pass conversational filler like 'Okay', 'Thanks', or 'What is my ticket ID'."
                },
                "priority": {"type": "string", "description": "Priority level: P1-Critical, P2-High, P3-Medium, P4-Low"},
                "caller_number": {"type": "string", "description": "Caller mobile number or Employee ID"}
            }
        )

        self.register_tool(
            name="get_symphony_ticket_status",
            handler=self._get_symphony_ticket_status,
            description="Check the current status, full description, assigned engineer, and resolution SLA of an existing Symphony Incident Ticket.",
            parameters={
                "ticket_id": {"type": "string", "description": "The Symphony ticket ID (e.g. 84920 or 1268637)"}
            }
        )

        self.register_tool(
            name="list_symphony_incidents",
            handler=self._list_symphony_incidents,
            description="List or search all active and past IT incident tickets for a caller from Symphony Summit Incident System.",
            parameters={
                "caller_email": {"type": "string", "description": "Caller email address or Employee ID"},
                "status_filter": {"type": "string", "description": "Filter by status: OPEN, CLOSED, or ALL"}
            }
        )

        # ─── Electricity Utility Client CRM Tools ──────────────────────────────────────────
        self.register_tool(
            name="crm_lookup_consumer",
            handler=self._crm_lookup_consumer,
            description="Lookup consumer account details, active meter status, and address from the Client Electricity CRM.",
            parameters={
                "consumer_id": {"type": "string", "description": "12-digit Consumer Account Number or registered mobile number"}
            }
        )
        
        self.register_tool(
            name="crm_register_complaint",
            handler=self._crm_register_complaint,
            description="Register a new electricity complaint (Power Outage, Meter Fault, Voltage Fluctuation, Billing Error) in Client CRM.",
            parameters={
                "consumer_id": {"type": "string", "description": "Consumer Account Number"},
                "category": {"type": "string", "description": "Complaint category: Power Outage, Meter Fault, Voltage Issue, Billing, Transformer"},
                "description": {"type": "string", "description": "Detailed description of the issue provided by consumer"},
                "sub_division": {"type": "string", "description": "Sub-division or locality name (e.g. Pune Central, Nagpur East, Nashik Road)"}
            }
        )
        
        self.register_tool(
            name="crm_check_feeder_status",
            handler=self._crm_check_feeder_status,
            description="Check the real-time electrical grid substation / feeder status for power outage reporting.",
            parameters={
                "feeder_code": {"type": "string", "description": "Substation or Feeder Code / Locality name"}
            }
        )

        # ─── Neox Helpdesk Solution Tools ────────────────────────────────────────────────
        self.register_tool(
            name="neox_create_ticket",
            handler=self._neox_create_ticket,
            description="Create or escalate a support ticket in Neox Helpdesk Solution.",
            parameters={
                "consumer_id": {"type": "string", "description": "Consumer ID or Phone Number"},
                "issue_summary": {"type": "string", "description": "Short summary of the issue"},
                "priority": {"type": "string", "description": "Priority level: High, Medium, Low, Critical"}
            }
        )
        
        self.register_tool(
            name="neox_check_agent_availability",
            handler=self._neox_check_agent_availability,
            description="Check live CSR agent queue availability in Neox Helpdesk for live call transfer.",
            parameters={
                "queue_name": {"type": "string", "description": "CSR Queue Name: Inbound_Support, Billing_Escalations, Technical_Line"}
            }
        )
        
        self.register_tool(
            name="neox_transfer_to_agent",
            handler=self._neox_transfer_to_agent,
            description="Transfer the active call to a Neox Helpdesk live Customer Service Representative (CSR).",
            parameters={
                "queue_name": {"type": "string", "description": "Target Neox CSR Queue"},
                "reason": {"type": "string", "description": "Reason for transferring to live CSR"}
            }
        )

        self.register_tool(
            name="send_sms_notification",
            handler=self._send_sms_notification,
            description="Send an instant SMS acknowledgement to the consumer with ticket number or service status.",
            parameters={
                "phone_number": {"type": "string", "description": "Consumer mobile number"},
                "message_text": {"type": "string", "description": "SMS message body"}
            }
        )

        # NOTE: create_symphony_ticket is registered once above. A second registration
        # here previously overwrote it with a weaker LLM-facing description — removed.

        # ─── Incident update (resolve / reopen / note) — GATED ──────────────────────────
        # Mutates real tickets; the valid status enum must be confirmed against the
        # customer's Summit workflow first. Off by default.
        if os.getenv("SUMMIT_ENABLE_UPDATES", "false").lower() == "true":
            self.register_tool(
                name="update_symphony_ticket",
                handler=self._update_symphony_ticket,
                description="Update an existing IT incident: change status (resolve, reopen, progress) or add a note. Use when the caller asks to close, reopen, or update a specific ticket number they own.",
                parameters={
                    "ticket_id": {"type": "string", "description": "The ticket number to update"},
                    "status": {"type": "string", "description": "New status, e.g. Resolved, Reopen, In-Progress"},
                    "note": {"type": "string", "description": "Optional progress or resolution note"},
                }
            )

        # ─── Legacy / Compatibility Tools ───────────────────────────────────────────────
        self.register_tool(
            name="check_complaint_status",
            handler=self._mock_check_complaint_status,
            description="Check the status of an existing electricity complaint or SR ticket number.",
            parameters={
                "ticket_number": {"type": "string", "description": "The SR or Complaint ticket number (e.g. MSEDCL-998234)"}
            }
        )

        self.register_tool(
            name="transfer_to_human",
            handler=self._mock_transfer_to_human,
            description="Initiate a transfer to a live human escalation specialist.",
            parameters={
                "reason": {"type": "string", "description": "Reason for transfer"}
            }
        )

        # ─── Medical Receptionist Tools ────────────────────────────────────────────────
        self.register_tool(
            name="find_doctor",
            handler=self._find_doctor,
            description="Find the right doctor for a patient's medical problem from the clinic directory. Call this once you know the patient's symptom/problem to match it to a specialty and return available doctors with their slots. Returns doctor name, specialty, and availability.",
            parameters={
                "problem": {"type": "string", "description": "The patient's medical problem or symptom in their own words (e.g. 'chest pain', 'skin rash', 'child fever')."}
            }
        )

        self.register_tool(
            name="schedule_appointment",
            handler=self._schedule_appointment,
            description="Book a doctor appointment for the patient. Call this ONLY after you have the patient's name, their problem, the chosen doctor, and a preferred day/time slot. Returns a confirmation with the appointment ID and time.",
            parameters={
                "patient_name": {"type": "string", "description": "Full name of the patient."},
                "problem": {"type": "string", "description": "Short description of the medical problem."},
                "doctor": {"type": "string", "description": "Name or specialty of the doctor to book."},
                "appointment_time": {"type": "string", "description": "Chosen day and time, e.g. 'Tuesday 2:30 PM' or 'Thu 10:30'."},
                "is_new_patient": {"type": "string", "description": "'yes' if this is a new patient being registered, 'no' if an existing patient."}
            }
        )

        self.register_tool(
            name="lookup_appointment",
            handler=self._lookup_appointment,
            description="Look up an existing booked appointment. Use when the caller asks to check or retrieve an appointment (e.g. 'is there an appointment for Dr. Priya at 11am', 'do I have an appointment'). Returns the matching patient, doctor and time.",
            parameters={
                "doctor": {"type": "string", "description": "Doctor name or specialty to search for (optional)."},
                "appointment_time": {"type": "string", "description": "Day and/or time to search for, e.g. 'Monday 11 AM' (optional)."},
                "patient_name": {"type": "string", "description": "Patient name to search for (optional)."},
                "caller_number": {"type": "string", "description": "Caller's phone number, to scope to their own appointments (optional)."}
            }
        )

        self.register_tool(
            name="create_kyc_session",
            handler=self._create_kyc_session,
            description="Start identity (KYC) verification for the patient on this call. Creates a one-time code the patient types at the verification page on their phone (Aadhaar Secure QR + live selfie). Use BEFORE booking an appointment.",
            parameters={
                "claimed_name": {"type": "string", "description": "The patient's name as they told us on the call."}
            }
        )

        self.register_tool(
            name="send_kyc_link_email",
            handler=self._send_kyc_link_email,
            description="Email the patient the identity-verification (KYC) link with their code already filled in, so they don't have to type a spoken web address. First ASK for their email and have them say it, then READ IT BACK to confirm, then call this with their full email in the 'email' argument. Preferred over reading the code aloud.",
            parameters={
                "email": {"type": "string", "description": "The patient's full email address exactly as spoken, written in standard form, e.g. 'name@gmail.com'. Convert spoken words to symbols: 'at' to @, 'dot' to a period. This argument is required and must contain the address."}
            }
        )

        self.register_tool(
            name="get_kyc_status",
            handler=self._get_kyc_status,
            description="Check whether the patient on this call has completed identity (KYC) verification.",
            parameters={
                "call_id": {"type": "string", "description": "The current call id (injected by the system)."}
            }
        )

        self.register_tool(
            name="verify_insurance",
            handler=self._verify_insurance,
            description="Verify a caller's health insurance policy and compute the co-payment for a consultation fee. (Demo: uses a fabricated insurer list.)",
            parameters={
                "insurer": {"type": "string", "description": "Insurance provider name, e.g. 'Global Health Secure'."},
                "member_id": {"type": "string", "description": "Policy / member ID."},
                "fee": {"type": "string", "description": "Consultation fee in rupees for the chosen doctor."}
            }
        )

        self.register_tool(
            name="take_payment",
            handler=self._take_payment,
            description="Confirm a pre-booking payment (co-pay or consultation fee). (Demo: simulated, no real money moves.)",
            parameters={
                "amount": {"type": "string", "description": "Amount in rupees to confirm as paid."}
            }
        )

        self.register_tool(
            name="reschedule_appointment",
            handler=self._reschedule_appointment,
            description="Move an existing booked appointment to a new day/time. Use when the caller asks to reschedule, change, move or postpone an appointment.",
            parameters={
                "new_time": {"type": "string", "description": "New day and time, e.g. 'Friday 10 AM' (optional; if absent, ask the caller)."},
                "appointment_id": {"type": "string", "description": "Booking reference like APT-23306 (optional)."},
                "caller_number": {"type": "string", "description": "Caller's phone number (optional)."},
                "patient_name": {"type": "string", "description": "Patient name on the appointment (optional)."}
            }
        )

        # ─── Sanjeevani Patient-Medication Care Tools (tenant-scoped, outbound) ─────────────
        self.register_tool(
            name="confirm_identity",
            handler=self._sanjeevani_confirm_identity,
            description="Confirm the patient's identity by checking the date of birth they say against the record. Call this FIRST, before reading any medicine details. On a match, identity is unlocked for the rest of the call.",
            parameters={
                "answer": {"type": "string", "description": "The date of birth the person said, written with the day, month and year, for example 14 March 2023 or 14/03/2023."}
            }
        )
        self.register_tool(
            name="get_prescription_context",
            handler=self._sanjeevani_get_prescription_context,
            description="Get the patient's first name and the list of their medicines (dose, timing, duration) to explain on the call. REFUSES until identity has been confirmed on this call. Never read a dose for an item the record marks as needing clarification.",
            parameters={
                "task_id": {"type": "string", "description": "The follow-up task id for this call (optional; the system can resolve it)."}
            }
        )
        self.register_tool(
            name="look_up_care_information",
            handler=self._sanjeevani_look_up,
            description="Look up an answer in this company's own care documents (medicine guide, care sheets, policies) when the patient asks a question. Refuses until identity is confirmed. If nothing is found, say it is a question for the doctor or pharmacist.",
            parameters={
                "question": {"type": "string", "description": "The patient's question, in a few words."}
            }
        )
        self.register_tool(
            name="record_understanding",
            handler=self._sanjeevani_record_understanding,
            description="Record whether the patient understood a specific medicine after it was explained.",
            parameters={
                "item_id": {"type": "string", "description": "The prescription item id that was explained."},
                "understood": {"type": "string", "description": "'yes' if the patient understood, otherwise 'no'."}
            }
        )
        self.register_tool(
            name="record_feedback",
            handler=self._sanjeevani_record_feedback,
            description="Record the patient's feedback on a medicine: whether they took it, whether it helped, their satisfaction (1-5), and any side effects. If the answer is unhappy (not recovered, low satisfaction, side effects, or they want a doctor) this also arranges a doctor meeting and raises a care-team ticket.",
            parameters={
                "prescription_id": {"type": "string", "description": "The prescription id (optional if item_id given)."},
                "item_id": {"type": "string", "description": "The prescription item id this feedback is about (optional)."},
                "taken": {"type": "string", "description": "Did they take it: yes, partly, no, or not_taken."},
                "helped": {"type": "string", "description": "Did it help: yes, partly, or no."},
                "satisfaction": {"type": "string", "description": "Satisfaction from 1 (poor) to 5 (excellent)."},
                "side_effects": {"type": "string", "description": "Any side effects they mention, in their words (empty if none)."},
                "wants_doctor": {"type": "string", "description": "'yes' if they asked to see a doctor."}
            }
        )
        self.register_tool(
            name="request_doctor_meeting",
            handler=self._sanjeevani_request_doctor_meeting,
            description="Request a meeting with the patient's doctor. Defaults to the prescribing doctor. Collects preferred times but NEVER invents a slot — the clinic confirms within one working day.",
            parameters={
                "reason": {"type": "string", "description": "Why the patient wants to see the doctor."},
                "preferred_times": {"type": "string", "description": "Day/time preferences the patient gave (optional)."}
            }
        )
        self.register_tool(
            name="schedule_callback",
            handler=self._sanjeevani_schedule_callback,
            description="Schedule a later callback when the patient is busy or asks to be called another time.",
            parameters={
                "when": {"type": "string", "description": "When the patient would like to be called back, in their words."}
            }
        )
        self.register_tool(
            name="mark_do_not_call",
            handler=self._sanjeevani_mark_do_not_call,
            description="Record that the patient does not want to be called again. Stops all future follow-up calls to them.",
            parameters={
                "confirm": {"type": "string", "description": "'yes' to confirm the patient asked not to be called."}
            }
        )

        # ─── The company's documents, for an agent that is not a set of desks ─────────────────
        self.register_tool(
            name="search_company_documents",
            handler=self._search_company_documents,
            description="Look up an answer in the company's own documents (its Knowledge section) when the caller asks about the business: "
                        "its products, prices, policies, hours or procedures. Answer only from what it returns. If it returns nothing, say you "
                        "do not have that and offer to take a message.",
            parameters={
                "question": {"type": "string", "description": "What to look up, in a few words."}
            }
        )

    def register_tool(self, name: str, handler: Callable, description: str, parameters: dict):
        """Register a new tool."""
        cleaned_params = {}
        for k, v in parameters.items():
            cleaned_params[k] = {
                "type": v.get("type", "string").lower(),
                "description": v.get("description", "")
            }
        optional = {k for k, v in parameters.items() if v.get("optional")}      # left out of "required" in the schema

        self._tools[name] = {
            "handler": handler,
            "schema": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": cleaned_params,
                    "required": [k for k in cleaned_params if k not in optional]
                }
            }
        }

    async def execute(self, name: str, args: dict) -> dict:
        """Execute a registered tool by name with arguments."""
        if name not in self._tools:
            return {"error": f"Tool '{name}' not found."}
            
        handler = self._tools[name]["handler"]
        try:
            # The router/LLM sometimes injects args a given tool doesn't accept
            # (e.g. caller_number into list_symphony_incidents). Drop unknown kwargs
            # instead of crashing with a TypeError.
            try:
                sig = inspect.signature(handler)
                accepts_kwargs = any(
                    p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
                )
                if not accepts_kwargs:
                    allowed = set(sig.parameters.keys())
                    dropped = [k for k in args if k not in allowed]
                    if dropped:
                        logger.warning(f"[ToolExecutor] '{name}': dropping unsupported args {dropped}")
                    args = {k: v for k, v in args.items() if k in allowed}
            except (TypeError, ValueError):
                pass
            if inspect.iscoroutinefunction(handler):
                result = await handler(**args)
            else:
                result = handler(**args)
            if isinstance(result, tuple) and len(result) == 2:
                # (what to say, what to show): a link goes on the console transcript, the model is told only to read the code
                result, display = result
                return {"status": "success", "result": result, "display": display}
            return {"status": "success", "result": result}
        except Exception as e:
            logger.error(f"[ToolExecutor] Execution error for '{name}': {e}")
            return {"error": f"Tool execution failed: {str(e)}"}

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        """Return schema definitions of available tools for LLM binding."""
        return [tool["schema"] for tool in self._tools.values()]

    def get_tool_schemas(self, names: List[str] = None) -> list:
        """Get schema declarations filtered by names."""
        schemas = []
        for name, tool in self._tools.items():
            if names is None or name in names:
                schemas.append(tool["schema"])
        return schemas

    # ─── Symphony Incident Management Handlers ────────────────────────────────────────

    async def _get_symphony_ticket_status(self, ticket_id: str) -> str:
        from .summit_mcp_server import get_incident_description
        res = await get_incident_description(ticket_id=ticket_id)
        if res.get("status") != "SUCCESS":
            return f"I could not find any details for ticket {ticket_id}. Please check the number, or I can look up your open tickets instead."
        t_id = res.get("ticket_id", ticket_id)
        sym = res.get("symptom", "IT Issue")
        desc = res.get("description", "")
        status_val = res.get("current_status", "In Progress")
        assigned = res.get("assigned_engineer", "IT Service Desk")
        priority = res.get("priority", "P3-Medium")
        sla = res.get("resolution_deadline", "Within 4 Hours")

        out = f"Symphony Ticket #{t_id} [{priority}]: Status '{status_val}'. Assigned to: {assigned}. Summary: {sym}."
        if desc and desc != sym:
            out += f" Full Description: {desc}."
        if sla:
            out += f" Target SLA Deadline: {sla}."
        return out

    async def _list_symphony_incidents(self, caller_email: str = "user@example.com", status_filter: str = "OPEN") -> str:
        from .summit_mcp_server import list_incidents
        res = await list_incidents(caller_email=caller_email, status_filter=status_filter)
        tickets = res.get("tickets", [])
        if not tickets:
            return f"No {status_filter} incident tickets found in Symphony Summit for {caller_email}."

        summary_items = []
        for t in tickets[:5]:
            t_no = t.get("ticket_no") or t.get("TicketNo", "N/A")
            sym = t.get("symptom") or t.get("Symptom", "IT Issue")
            st = t.get("status") or t.get("Status", "Open")
            pri = t.get("priority") or t.get("Priority", "P3")
            summary_items.append(f"Ticket #{t_no} [{pri}]: {sym} (Status: {st})")
        return f"Found {len(tickets)} {status_filter} Symphony Ticket(s) for {caller_email}:\n- " + "\n- ".join(summary_items)

    # ─── CRM & Neox Mock Handlers ────────────────────────────────────────────────────
    
    async def _crm_lookup_consumer(self, consumer_id: str) -> str:
        names = ["Rajesh Patil", "Sunita Sharma", "Aniket Deshmukh", "Priya Kulkarni", "Amitabh Joshi"]
        cities = ["Pune Central", "Mumbai Suburban", "Nagpur East", "Nashik Division", "Aurangabad Zone"]
        chosen_name = names[hash(consumer_id) % len(names)]
        chosen_city = cities[hash(consumer_id) % len(cities)]
        meter_status = "Active - Dual Phase Meter #MTR-88492"
        last_bill = "Rs. 1,450 (Paid)"
        return f"Client CRM Account Found: Name: {chosen_name}, Location: {chosen_city}, Consumer ID: {consumer_id}, Meter Status: {meter_status}, Last Bill: {last_bill}."

    async def _crm_register_complaint(self, consumer_id: str, category: str, description: str, sub_division: str) -> str:
        ticket_num = f"MSEDCL-2026-{random.randint(100000, 999999)}"
        return f"Complaint Registered Successfully in Client CRM! Ticket Number: {ticket_num}. Category: {category}. Assigned Sub-Division: {sub_division}. Technician Dispatch SLA: 2 Hours. SMS sent to consumer."

    async def _crm_check_feeder_status(self, feeder_code: str) -> str:
        statuses = [
            f"Feeder '{feeder_code}' status is NORMAL (11kV Grid operational, zero planned outages).",
            f"ALERT: Feeder '{feeder_code}' is currently experiencing a TRIPPED OUTAGE due to monsoon storm. Restoration work in progress (Estimated restoration: 45 mins)."
        ]
        return statuses[hash(feeder_code) % len(statuses)]

    async def _neox_create_ticket(self, consumer_id: str, issue_summary: str, priority: str) -> str:
        neox_id = f"NEOX-TKT-{random.randint(50000, 99999)}"
        return f"Ticket Created in Neox Helpdesk Solution! Neox Ticket ID: {neox_id}. Consumer: {consumer_id}. Priority: {priority}. Status: Assigned to Tier-2 CSR Support."

    async def _neox_check_agent_availability(self, queue_name: str) -> str:
        return f"Neox Helpdesk Queue '{queue_name}': 4 Live CSR Agents Available. Estimated wait time: < 15 seconds."

    async def _neox_transfer_to_agent(self, queue_name: str, reason: str) -> str:
        supervisor = get_config().telephony.supervisor_transfer_number
        return f"TRANSFER_NEOX_CSR:Queue={queue_name}:Reason={reason}:Phone={supervisor}"

    async def _send_sms_notification(self, phone_number: str, message_text: str) -> str:
        return f"SMS Sent to {phone_number}: '{message_text}' (Delivered via Utility Gateway)."

    def _hzl_url(self) -> str:
        url = os.getenv("SYMPHONY_CLOUD_FUNCTION_URL", "").strip()
        # The env value sometimes points at the raw Summit REST WCF endpoint, which uses
        # a different contract; the action/params contract below targets the hzl helper.
        if "hzl-ticket-helper" not in url:
            url = "https://hzl-ticket-helper-160334972979.us-central1.run.app"
        return url

    async def _hzl_auth_headers(self, client: "httpx.AsyncClient", url: str) -> dict:
        token_resp = await client.get(
            "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity",
            params={"audience": url}, headers={"Metadata-Flavor": "Google"}, timeout=5.0)
        token_resp.raise_for_status()
        return {"Authorization": f"Bearer {token_resp.text}", "Content-Type": "application/json"}

    async def warm_ticketing(self) -> None:
        """Wake the ticketing cloud function in the background (a cold start once exceeded the 20 s timeout)."""
        try:
            hzl_url = self._hzl_url()
            if not hzl_url:
                return
            async with httpx.AsyncClient() as client:
                headers = await self._hzl_auth_headers(client, hzl_url)
                await client.post(hzl_url, json={"action": "get_incident_details", "params": {"ticket_no": "0"}},
                                  headers=headers, timeout=30.0)
        except Exception as exc:
            logger.debug(f"[Symphony warm-up] {type(exc).__name__}: {exc}")

    async def _create_symphony_ticket(self, issue_description: str, priority: str = "P3-Medium", caller_number: str = "") -> str:
        import hashlib, time, asyncio
        from datetime import datetime

        hzl_url = self._hzl_url()
        caller_email = os.getenv("SUMMIT_DEFAULT_CALLER_EMAIL", "caller@example.com").strip()

        # Idempotency: if the same caller raised an identical issue seconds ago, return
        # that ticket rather than creating a duplicate (seen on real calls).
        idem_key = hashlib.sha1(f"{caller_number}|{(issue_description or '').strip().lower()}".encode(), usedforsecurity=False).hexdigest()
        prev = _RECENT_CREATES.get(idem_key)
        if prev and (time.time() - prev[1]) < _IDEMPOTENCY_WINDOW_SECS:
            return (f"You already have ticket number {prev[0]} open for this exact issue, raised moments ago. "
                    f"I won't create a duplicate — is there anything else I can help with?")

        if time.time() - _UNCERTAIN_CREATES.get(idem_key, 0) < 1800:
            issue_description = "NOTE: an earlier attempt for this issue timed out and may also exist. " + issue_description
        ticket_id = None
        try:
            async with httpx.AsyncClient() as client:
                auth_headers = await self._hzl_auth_headers(client, hzl_url)
                r = await client.post(
                    hzl_url,
                    json={
                        "action": "create_incident_via_api_key",
                        "params": {
                            "caller_email": caller_email,
                            "symptom": issue_description[:100],
                            "description": f"Urgency: {priority}. Issue: {issue_description}"
                        }
                    },
                    headers=auth_headers,
                    timeout=float(os.getenv("SUMMIT_CREATE_TIMEOUT_S", "15"))
                )
                r.raise_for_status()
                data = r.json()
                ticket_no = data.get("TicketNo") or data.get("TicketID_Internal")
                if not ticket_no or data.get("Errors"):
                    raise RuntimeError(f"Symphony rejected the request: {data.get('Errors') or data.get('Message')}")
                ticket_id = str(ticket_no)
        except Exception as e:
            # NEVER fabricate a ticket number. Report honestly so the caller is not told a
            # ticket exists when it does not.
            logger.error(f"[Symphony Summit API] Ticket creation failed: {type(e).__name__} - {e}")
            if isinstance(e, httpx.TimeoutException):
                _UNCERTAIN_CREATES[idem_key] = time.time()
                logger.warning(f"[Symphony Summit API] POSSIBLE DUPLICATE: create timed out for caller {caller_number}; "
                               f"Summit may have raised the ticket anyway. Issue: {issue_description[:120]}")
                return ("I'm sorry, the ticketing system is responding slowly, so I can't confirm your ticket was raised. "
                        "Would you like me to try again?")
            return ("I'm sorry, I could not reach the ticketing system just now, so I have not been able to "
                    "raise your ticket. Would you like me to try again?")

        _RECENT_CREATES[idem_key] = (ticket_id, time.time())

        # Persist locally with the REAL caller number (so the ticket is findable by phone).
        try:
            from .database import async_session
            from .models import Ticket
            from .websocket_manager import dashboard_manager
            async with async_session() as db:
                ticket = Ticket(
                    ticket_id=ticket_id,
                    caller_number=(caller_number or "unknown"),
                    intent="Incident Management System",
                    issue=issue_description,
                    priority=priority,
                    transcript_summary=f"Symphony Summit Ticket: {issue_description}",
                    sentiment="Urgent",
                    created_at=datetime.utcnow()
                )
                db.add(ticket)
                await db.commit()
                await dashboard_manager.broadcast({"type": "ticket_created", "ticket": ticket.to_dict()})
        except Exception as db_exc:
            logger.warning(f"[Symphony DB Save] Failed to persist ticket: {db_exc}")

        # Enrich with the real SLA/priority/engineer in the BACKGROUND — the ~10-15s
        # read-back round-trip must not keep the caller waiting mid-call.
        asyncio.create_task(self._enrich_ticket_async(ticket_id, hzl_url))

        return (
            f"Incident Ticket Created Successfully! Your ticket number is {ticket_id}. "
            f"Priority: {priority}. It has been logged and assigned to our IT support team."
        )

    # ─── Medical Receptionist Handlers ─────────────────────────────────────────────
    def _load_doctors(self) -> list:
        """Load the fabricated doctor directory (and insurers) for the medical agent."""
        self._insurers = {}
        try:
            import json
            path = os.path.join(os.path.dirname(__file__), "data", "doctors.json")
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            docs = data.get("doctors", [])
            self._insurers = data.get("insurers", {})
            logger.info(f"[MedicalKB] Loaded {len(docs)} doctors, {len(self._insurers)} insurers.")
            return docs
        except Exception as e:
            logger.warning(f"[MedicalKB] Could not load doctors.json: {e}")
            return []

    def _rec_line(self, doc: dict, lead: str = "For that, I'd recommend") -> str:
        avail = _spoken_availability(doc.get("availability", []))
        specialty = doc.get("specialty", "").strip()
        article = "an" if specialty[:1].lower() in "aeiou" else "a"
        return f"{lead} {doc['name']}, {article} {specialty}. They're available {avail}."

    async def _find_doctor(self, problem: str = "", preferred: str = "", **kwargs) -> str:
        """Match a patient's problem to the best-fit doctor and return availability.

        If `preferred` (a doctor name or specialty the caller explicitly asked for) is given,
        honor it over symptom-based routing; if we don't carry that specialty, say so."""
        if not self._doctors:
            return "I'm sorry, the doctor directory is unavailable right now."

        # 1) Explicit request for a named doctor or specialty wins.
        pref = (preferred or "").lower().replace("dr.", "").replace("dr ", "").strip()
        if pref:
            for doc in self._doctors:
                if pref in doc.get("name", "").lower() or pref in doc.get("specialty", "").lower():
                    return self._rec_line(doc, lead="Of course — I'd book you with")
            return (f"I'm sorry, we don't have a {preferred} at the clinic. "
                    f"Our team covers general medicine, cardiology, dermatology, orthopedics, "
                    f"pediatrics, E N T, neurology, gastroenterology and dentistry. "
                    f"Would you like me to suggest the right specialist for your symptom instead?")

        p = (problem or "").lower()
        # 2) Pediatric cue (a child/age mention) strongly routes to the pediatrician.
        peds_cues = ["year old", "years old", "year-old", "month old", "months old", "toddler",
                     "my child", "my son", "my daughter", "my kid", "infant", "baby"]
        if any(c in p for c in peds_cues):
            peds = next((d for d in self._doctors if "pediatric" in d.get("specialty", "").lower()), None)
            if peds:
                return self._rec_line(peds)

        # 3) Keyword scoring over the directory.
        best, best_score = None, 0
        for doc in self._doctors:
            score = sum(2 for kw in doc.get("treats", []) if kw in p)
            if doc.get("specialty", "").lower() in p:
                score += 3
            if score > best_score:
                best_score, best = score, doc
        if not best:
            best = next((d for d in self._doctors if "general" in d.get("specialty", "").lower()), self._doctors[0])
        return self._rec_line(best)

    async def _schedule_appointment(self, patient_name: str = "", problem: str = "",
                                    doctor: str = "", appointment_time: str = "",
                                    is_new_patient: str = "yes",
                                    call_id: Any = None, caller_number: str = "", **kwargs) -> str:
        """Book a doctor appointment, persist it, and return a spoken confirmation."""
        dsel = (doctor or "").lower()
        matched = None
        for d in self._doctors:
            name_l, spec_l = d.get("name", "").lower(), d.get("specialty", "").lower()
            if dsel and (dsel in name_l or dsel in spec_l or (spec_l and spec_l in dsel)):
                matched = d
                break
        doctor_name = matched["name"] if matched else (doctor or "the assigned doctor")
        doctor_specialty = matched["specialty"] if matched else ""
        appt_id = f"APT-{random.randint(10000, 99999)}"
        new_flag = str(is_new_patient).strip().lower() in ("yes", "true", "1", "new", "y")

        cid = None
        try:
            cid = int(call_id) if call_id is not None else None
        except (TypeError, ValueError):
            cid = None

        # ── Identity gate (hard precondition): do NOT book until the patient is verified, whenever
        # KYC is available. This protects every booking path (LLM-driven or scripted). If KYC is
        # off / the sidecar is down, kyc_available() is False and booking proceeds (fail-open).
        if cid is not None:
            try:
                from . import kyc_core
                if await kyc_core.kyc_available() and not kyc_core.kyc_skipped_for_channel(caller_number):
                    kst = await kyc_core.status_for_call(cid)
                    if kst.get("state") != "verified":
                        code = kst.get("code")  # present only while a session is active
                        if not code and kst.get("state") in ("none", None):
                            sess = await kyc_core.create_session(cid, caller_number or "", patient_name or "")
                            code = sess.get("code") if sess else None
                        if code:
                            return (f"Before I can confirm the booking I just need to verify your identity. "
                                    f"On your phone, open {kyc_core.spoken_url()} and enter this code: "
                                    f"{kyc_core.spoken_code(code)}. Tell me once you're done and I'll lock it in.")
                        return ("I just need your identity check to finish before I can confirm the booking — "
                                "please complete the steps on your phone, then tell me you're done.")
            except Exception as kyc_pre_exc:
                logger.debug(f"[Appointment] KYC precondition skipped: {kyc_pre_exc}")

        # If the patient completed identity verification on this call, record it on the booking.
        kyc_note = None
        if cid is not None:
            try:
                from . import kyc_core
                st = await kyc_core.status_for_call(cid)
                if st.get("state") == "verified":
                    sm = st.get("summary") or {}
                    src = ('DEMO credential' if sm.get('demo') else
                           'UNSIGNED Aadhaar document, NOT cryptographically verified - check original ID at visit'
                           if sm.get('trust') == 'document' else 'UIDAI Aadhaar Secure QR')
                    kyc_note = (f"Identity verified ({src}"
                                f", Aadhaar XXXX{sm.get('aadhaar_last4') or '----'}, face match {sm.get('face_score')})")
            except Exception as kyc_exc:
                logger.debug(f"[Appointment] KYC lookup skipped: {kyc_exc}")

        try:
            from .database import async_session
            from .models import Appointment
            from .websocket_manager import dashboard_manager
            async with async_session() as db:
                appt = Appointment(
                    appointment_id=appt_id,
                    call_id=cid,
                    notes=kyc_note,
                    patient_name=patient_name or None,
                    caller_number=caller_number or None,
                    is_new_patient=new_flag,
                    problem=problem or None,
                    doctor_name=doctor_name,
                    doctor_specialty=doctor_specialty,
                    appointment_time=appointment_time or None,
                    status="scheduled",
                    created_at=datetime.utcnow(),
                )
                db.add(appt)
                await db.commit()
                await dashboard_manager.broadcast({"type": "appointment_created", "appointment": appt.to_dict()})
                # Background Sarvam name correction via Smartflo CDR recording
                if cid:
                    asyncio.create_task(self._launch_name_correction(
                        appt.id, appt_id, cid, patient_name or ""))
        except Exception as db_exc:
            logger.warning(f"[Appointment DB Save] Failed to persist appointment: {db_exc}")

        when = appointment_time or "the slot we discussed"
        spec = f", our {doctor_specialty}," if doctor_specialty else ""
        # Spell the reference so it reads as a number the caller can jot down,
        # not as "forty-one thousand": APT-41108 -> "A P T, 4 1 1 0 8".
        digits = " ".join(list(appt_id.split("-")[-1]))
        ref_spoken = f"A P T, {digits}"
        greeting = f"You're all set, {patient_name}! " if patient_name else "You're all set! "
        return (f"{greeting}Your appointment with {doctor_name}{spec} is confirmed for {when}. "
                f"Your booking reference is {ref_spoken}.")

    async def _launch_name_correction(self, appt_row_id: int, appt_id: str,
                                      db_call_id: int, original_name: str):
        """Look up call_sid from DB, then kick off Sarvam name correction."""
        try:
            from .database import async_session
            from .models import Call
            async with async_session() as db:
                call = await db.get(Call, db_call_id)
                call_sid = call.call_sid if call else None
            if not call_sid:
                logger.info(f"[SarvamNameFix] No call_sid for db_call_id={db_call_id}")
                return
            from .sarvam_name_corrector import correct_appointment_name
            await correct_appointment_name(appt_row_id, appt_id, call_sid, original_name)
        except Exception as e:
            logger.error(f"[SarvamNameFix] Launch failed for {appt_id}: {e}")

    async def _lookup_appointment(self, doctor: str = "", appointment_time: str = "",
                                  patient_name: str = "", caller_number: str = "", **kwargs) -> str:
        """Search booked appointments by doctor / time / patient / caller, speak the match."""
        try:
            from .database import async_session
            from .models import Appointment
            from sqlalchemy import select
            async with async_session() as db:
                rows = (await db.execute(
                    select(Appointment).order_by(Appointment.created_at.desc()))).scalars().all()
        except Exception as e:
            logger.warning(f"[Appointment Lookup] DB read failed: {e}")
            return "I'm sorry, I couldn't access the appointment records right now."

        def _n(s):
            return (s or "").lower().strip()

        d = _n(doctor).replace("dr.", "").replace("dr ", "").strip()
        pn = _n(patient_name)
        cn = _n(caller_number)
        # time query -> comparable tokens ("11 am" -> {"11","am"}), ignoring filler
        t_toks = [x for x in re.findall(r"[a-z0-9:]+", _n(appointment_time))
                  if x not in ("at", "the", "on", "around", "about")]

        matches = []
        for a in rows:
            if _n(a.status) in ("cancelled", "canceled"):
                continue
            if d and d not in _n(a.doctor_name) and d not in _n(a.doctor_specialty):
                continue
            if t_toks and not all(tok in _n(a.appointment_time) for tok in t_toks):
                continue
            if pn and pn not in _n(a.patient_name):
                continue
            if cn and cn not in _n(a.caller_number):
                continue
            matches.append(a)

        if not matches:
            return ("I couldn't find any appointment matching that. "
                    "Could you give me the patient's name or the booking reference?")
        matches = matches[:3]
        parts = []
        for a in matches:
            who = a.patient_name or "a patient"
            dname = a.doctor_name or "the doctor"
            when = a.appointment_time or "an unspecified time"
            parts.append(f"{who} with {dname} at {when}")
        lead = "I found one appointment: " if len(parts) == 1 else f"I found {len(parts)} appointments: "
        return lead + "; ".join(parts) + "."

    def _slot_valid_for(self, doctor_name: str, time_str: str):
        """Return (ok, avail_text): is time_str inside this doctor's real hours?"""
        key = (doctor_name or "").lower().replace("dr.", "").replace("dr ", "").strip()
        slots = []
        for d in self._doctors:
            if key and key in d.get("name", "").lower():
                slots = d.get("availability", [])
                break
        at = _spoken_availability(slots)
        if not slots:
            return True, at
        tl = (time_str or "").lower()
        day3 = next((a for a in ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] if a in tl), None)
        m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", tl)
        tmin = None
        if m:
            h = int(m.group(1))
            mi = int(m.group(2) or 0)
            ap = (m.group(3) or "").lower()
            if ap == "pm" and h < 12:
                h += 12
            if ap == "am" and h == 12:
                h = 0
            tmin = h * 60 + mi
        day_slots = [s for s in slots if s.split()[0][:3].lower() == day3] if day3 else slots
        if day3 and not day_slots:
            return False, at
        if tmin is not None:
            for s in day_slots:
                try:
                    a, b = s.split()[1].split("-")
                    if _hm_to_min(a) <= tmin < _hm_to_min(b):
                        return True, at
                except Exception:
                    pass
            return False, at
        return True, at

    async def _reschedule_appointment(self, new_time: str = "", appointment_id: str = "",
                                      caller_number: str = "", patient_name: str = "", **kwargs) -> str:
        """Move an existing appointment to a new time; persist and broadcast the change."""
        try:
            from .database import async_session
            from .models import Appointment
            from .websocket_manager import dashboard_manager
            from sqlalchemy import select

            def _n(s):
                return (s or "").lower().strip()

            aid = _n(appointment_id).replace("apt-", "").replace("apt ", "").replace(" ", "")
            cn, pn = _n(caller_number), _n(patient_name)
            async with async_session() as db:
                rows = (await db.execute(
                    select(Appointment).order_by(Appointment.created_at.desc()))).scalars().all()
                active = [a for a in rows if _n(a.status) not in ("cancelled", "canceled")]
                target = None
                if aid:
                    target = next((a for a in active
                                   if aid in _n(a.appointment_id).replace("apt-", "").replace(" ", "")), None)
                if not target and cn:
                    target = next((a for a in active if cn in _n(a.caller_number)), None)
                if not target and pn:
                    target = next((a for a in active if pn in _n(a.patient_name)), None)
                if not target:
                    return ("I couldn't find an existing appointment to reschedule. "
                            "Could you give me your booking reference, or the name it's under?")
                if not new_time:
                    return (f"I found your appointment with {target.doctor_name} on "
                            f"{target.appointment_time or 'the booked slot'}. "
                            f"What day and time would you like to move it to?")
                ok, avail = self._slot_valid_for(target.doctor_name, new_time)
                if not ok:
                    return (f"I'm sorry, {target.doctor_name} isn't available at {new_time}. "
                            f"They're available {avail}. What day and time would you like to move it to instead?")
                old = target.appointment_time
                target.appointment_time = new_time
                target.status = "rescheduled"
                await db.commit()
                await dashboard_manager.broadcast({"type": "appointment_updated", "appointment": target.to_dict()})
                return (f"Done — I've moved your appointment with {target.doctor_name} "
                        f"from {old or 'its earlier slot'} to {new_time}. "
                        f"Your booking reference stays the same.")
        except Exception as e:
            logger.warning(f"[Appointment Reschedule] failed: {e}")
            return "I'm sorry, I couldn't update that appointment just now. Please try again in a moment."

    def pick_specialty(self, problem: str = "", preferred: str = "") -> str:
        """Best-matching specialty for a problem, or a directly requested doctor/specialty."""
        if not self._doctors:
            return ""
        pref = (preferred or "").lower().replace("dr.", "").replace("dr ", "").strip()
        if pref:
            aliases = {
                "general medicine": "general", "general physician": "general", "gp": "general",
                "skin": "dermatolog", "heart": "cardiolog", "bone": "orthop",
                "child": "pediatr", "children": "pediatr", "kids": "pediatr",
                "stomach": "gastro", "digestive": "gastro", "neuro": "neurolog",
                "ear nose throat": "ent", "ear": "ent", "teeth": "dent", "dental": "dent",
            }
            terms = [pref] + [canon for alias, canon in aliases.items() if alias in pref]
            for d in self._doctors:
                dn, ds = d.get("name", "").lower(), d.get("specialty", "").lower()
                if any(t in dn or t in ds for t in terms):
                    return d.get("specialty", "")
            return ""  # requested specialty/doctor not in the directory
        p = (problem or "").lower()
        peds = ["year old", "years old", "year-old", "month old", "months old", "toddler",
                "my child", "my son", "my daughter", "my kid", "infant", "baby"]
        if any(c in p for c in peds):
            return "Pediatrician"
        best, bs = None, 0
        for d in self._doctors:
            s = sum(2 for kw in d.get("treats", []) if kw in p)
            if d.get("specialty", "").lower() in p:
                s += 3
            if s > bs:
                bs, best = s, d
        if best:
            return best.get("specialty", "")
        gp = next((d for d in self._doctors if "general" in d.get("specialty", "").lower()), None)
        return (gp or self._doctors[0]).get("specialty", "")

    def doctors_in(self, specialty: str) -> list:
        return [d for d in self._doctors if d.get("specialty", "").lower() == (specialty or "").lower()]

    def insurer_coverage(self, insurer: str):
        """(coverage_fraction, display_name) for a fabricated insurer, or (None, None)."""
        key = (insurer or "").lower().strip()
        for k, v in (getattr(self, "_insurers", {}) or {}).items():
            if k in key or key in k or v.get("name", "").lower() in key:
                return float(v.get("coverage", 0.0)), v.get("name", "")
        return None, None

    @staticmethod
    def _as_int(x) -> int:
        try:
            return int(float(str(x).lower().replace("rupees", "").replace("rs", "").replace(",", "").replace("inr", "").strip() or 0))
        except Exception:
            return 0

    async def _verify_insurance(self, insurer: str = "", member_id: str = "", fee: str = "0", **kwargs) -> str:
        """Verify a (fabricated) insurance policy and state the co-pay against a consultation fee."""
        fee_n = self._as_int(fee)
        cov, name = self.insurer_coverage(insurer)
        if cov is None:
            return (f"I'm sorry, I couldn't verify a policy with that provider. "
                    f"Would you like to pay the consultation fee of {fee_n} rupees directly instead?")
        copay = int(round(fee_n * (1 - cov)))
        billed = fee_n - copay
        return (f"Thank you — I've confirmed your {name} policy is active and covers specialist "
                f"consultations. Your plan covers {int(cov * 100)} percent, so your co-payment today is "
                f"{copay} rupees, and the remaining {billed} rupees is billed directly to your insurer.")

    async def _create_kyc_session(self, claimed_name: str = "", call_id: Any = None,
                                  caller_number: str = "", **kwargs) -> str:
        """Create a KYC session and return the spoken instructions (one-time code + page)."""
        from . import kyc_core
        try:
            cid = int(call_id) if call_id is not None else None
        except (TypeError, ValueError):
            cid = None
        sess = await kyc_core.create_session(cid, caller_number or "", claimed_name or "")
        if not sess:
            return "I'm sorry, I'm not able to start another verification for this call."
        return (f"Before I book your appointment, I need to verify your identity — it takes about two minutes. "
                f"Please keep your Aadhaar card with the QR code in hand. On your phone, open your browser and go to "
                f"{kyc_core.spoken_url()}, and enter this code: {kyc_core.spoken_code(sess['code'])}. "
                f"Once you've finished, just tell me you're done.")

    async def _send_kyc_link_email(self, email: str = "", call_id: Any = None,
                                   caller_number: str = "", claimed_name: str = "", **kwargs) -> str:
        """Email the KYC one-tap link (code pre-filled) so the patient doesn't type a spoken URL."""
        import re
        from . import kyc_core, emailer
        EMAIL_RE = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
        spoken_fallback = kwargs.get("spoken_fallback", "") or ""
        # 1) try the arg the model passed (normalizing spoken tokens); 2) if empty/bad, recover the
        # address from the caller's own transcript (audio models often don't fill the arg).
        candidate = self._normalize_spoken_email(email or "")
        if not re.match(EMAIL_RE, candidate):
            candidate = self._extract_email_from_speech(spoken_fallback or email or "")
        if not re.match(EMAIL_RE, candidate):
            logger.info("[email] couldn't parse address: arg=%r fallback=%r", (email or "")[:40],
                        spoken_fallback[:80])
            return ("I didn't quite catch that email. Could you say it again slowly — for example "
                    "'name at gmail dot com' — or I can read the code out instead?")
        email = candidate
        if not emailer.email_configured():
            return ("Email isn't set up on my side right now, so I'll read the code out instead. "
                    f"On your phone open {kyc_core.spoken_url()} and enter this code: ")
        try:
            cid = int(call_id) if call_id is not None else None
        except (TypeError, ValueError):
            cid = None
        # reuse an active session's code if one exists for this call, else start one
        code = None
        if cid is not None:
            st = await kyc_core.status_for_call(cid)
            code = st.get("code")
        if not code:
            sess = await kyc_core.create_session(cid, caller_number or "", claimed_name or "")
            code = sess.get("code") if sess else None
        if not code:
            return "I'm sorry, I'm not able to start another verification for this call right now."
        link = f"{kyc_core.PUBLIC_URL}?code={code}"
        ttl = getattr(kyc_core, "TTL_MIN", 15)
        subject = "Verify your identity — STL City Care"
        body = (
            "Hello,\n\n"
            "To verify your identity for your appointment, open the secure link below on your phone. "
            "Your code is already filled in — you'll just take a quick selfie and scan your Aadhaar QR.\n\n"
            f"Verification link: {link}\n"
            f"Verification code (OTP): {code}\n\n"
            f"This link and code expire in {ttl} minutes and can be used once.\n\n"
            "If you didn't request this, you can safely ignore this email.\n\n"
            "— STL City Care Clinic"
        )
        html = self._kyc_email_html(link=link, code=code, ttl=ttl)
        ok = await emailer.send(email, subject, body, html=html)
        if ok:
            return (f"Done — I've emailed the verification link to {email}. "
                    "Please open it on your phone, finish the quick selfie and Aadhaar scan, "
                    "and tell me once you're done.")
        return ("I couldn't get the email through just now. I can read the code out to you instead — "
                "would you like that?")

    @staticmethod
    def _normalize_spoken_email(raw: str) -> str:
        """Turn a spoken email ('aarit dot haldar at gmail dot com') into name.surname@gmail.com.
        Space-delimited replacements, so an already-clean address passes through untouched."""
        import re
        s = (raw or "").strip().lower()
        if not s:
            return s
        # common dictation of the domain
        s = s.replace("g mail", "gmail").replace("g-mail", "gmail")
        s = " " + s + " "
        for a, b in [(" at the rate ", "@"), (" at the rate", "@"), (" at sign ", "@"), (" at ", "@"),
                     (" underscore ", "_"), (" under score ", "_"),
                     (" dash ", "-"), (" hyphen ", "-"), (" minus ", "-"),
                     (" dot ", "."), (" dot", "."), (" period ", "."), (" point ", ".")]:
            s = s.replace(a, b)
        s = re.sub(r"\s+", "", s)      # drop any remaining spaces (incl. around a literal @)
        s = s.strip(".@")             # trim stray leading/trailing separators
        return s

    @staticmethod
    def _extract_email_from_speech(text: str) -> str:
        """Pull an email out of a whole spoken sentence, e.g. 'my email is a dot b at gmail dot com'
        or 'a a r i t at gmail dot com'. Returns '' if none found."""
        import re
        s = (text or "").strip().lower()
        if not s:
            return ""
        # already contains a literal email?
        m = re.search(r"[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}", s)
        if m:
            return m.group(0).strip(".")
        s = s.replace(" at the rate ", " @ ").replace(" at sign ", " @ ")
        s = re.sub(r"\bg\s*mail\b", "gmail", s)
        SEP = {"dot": ".", "period": ".", "point": ".", "underscore": "_", "under": "_",
               "dash": "-", "hyphen": "-", "minus": "-", "at": "@", "@": "@"}
        # filler words → boundaries. NB: single letters are NOT stopwords (spelled-out emails use them).
        STOP = {"my", "email", "e-mail", "mail", "is", "it's", "its", "the", "address", "sure",
                "yeah", "yes", "okay", "ok", "so", "um", "uh", "i'd", "like", "to", "use", "please",
                "send", "it", "that's", "thats", "you", "can", "could", "here", "hi", "hello",
                "of", "course", "sir", "maam", "ma'am"}
        mapped = []
        for t in s.split():
            t = t.strip(",.?!")
            if t in SEP:
                mapped.append(SEP[t])
            elif t in STOP:
                mapped.append(" ")
            elif re.fullmatch(r"[a-z0-9][a-z0-9._%+\-]*", t):
                # keep tokens that already contain separators, e.g. a transcribed "qa.tester"
                mapped.append(t)
            else:
                mapped.append(" ")
        joined = "".join(mapped)
        for chunk in joined.split():
            if "@" in chunk:
                chunk = chunk.strip(".@-_")
                if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", chunk):
                    return chunk
        return ""

    @staticmethod
    def _kyc_email_html(*, link: str, code: str, ttl: int) -> str:
        """Branded, email-client-safe HTML for the KYC email: verify button + OTP, inline styles only."""
        spaced = " ".join(list(code))  # render the OTP with gaps so it's easy to read
        return f"""\
<!DOCTYPE html>
<html lang="en">
<body style="margin:0;padding:0;background:#eef2f7;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#eef2f7;padding:28px 12px;">
    <tr><td align="center">
      <table role="presentation" width="480" cellpadding="0" cellspacing="0" style="max-width:480px;width:100%;background:#ffffff;border-radius:14px;overflow:hidden;box-shadow:0 6px 24px rgba(16,42,90,0.10);">
        <!-- header -->
        <tr><td style="background:linear-gradient(135deg,#1b4db3,#00a39c);background-color:#1b4db3;padding:26px 32px;">
          <div style="color:#ffffff;font-size:20px;font-weight:700;letter-spacing:0.3px;">STL&nbsp;City&nbsp;Care</div>
          <div style="color:#d6e4ff;font-size:12px;margin-top:3px;">Identity verification for your appointment</div>
        </td></tr>
        <!-- body -->
        <tr><td style="padding:30px 32px 8px 32px;">
          <h1 style="margin:0 0 10px 0;font-size:20px;color:#0f2a4a;">Verify your identity</h1>
          <p style="margin:0 0 20px 0;font-size:14px;line-height:1.6;color:#44556b;">
            You're almost booked in. Tap the button below on your phone to finish a quick,
            secure check — a selfie and an Aadhaar&nbsp;QR scan. Your code is already filled in.
          </p>
          <!-- OTP -->
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 22px 0;">
            <tr><td align="center" style="background:#f3f7fc;border:1px solid #dbe6f3;border-radius:10px;padding:16px;">
              <div style="font-size:11px;letter-spacing:1px;text-transform:uppercase;color:#6b7c93;margin-bottom:6px;">Your verification code</div>
              <div style="font-size:30px;font-weight:700;letter-spacing:8px;color:#1b4db3;font-family:Consolas,Menlo,monospace;">{spaced}</div>
            </td></tr>
          </table>
          <!-- button -->
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
            <a href="{link}" target="_blank"
               style="display:inline-block;background:#1b4db3;color:#ffffff;text-decoration:none;font-size:16px;font-weight:600;padding:14px 38px;border-radius:9px;">
              Verify now &rarr;
            </a>
          </td></tr></table>
          <p style="margin:18px 0 0 0;font-size:12px;line-height:1.6;color:#6b7c93;text-align:center;">
            Button not working? Open this link:<br>
            <a href="{link}" target="_blank" style="color:#1b4db3;word-break:break-all;">{link}</a>
          </p>
        </td></tr>
        <!-- note -->
        <tr><td style="padding:16px 32px 4px 32px;">
          <div style="background:#fff8ec;border:1px solid #f3e0bb;border-radius:8px;padding:12px 14px;font-size:12px;color:#8a6d3b;line-height:1.5;">
            This link and code expire in <strong>{ttl} minutes</strong> and can be used once. Never share them with anyone.
          </div>
        </td></tr>
        <!-- footer -->
        <tr><td style="padding:16px 32px 28px 32px;">
          <p style="margin:0;font-size:11px;line-height:1.6;color:#98a6ba;">
            If you didn't request this, you can safely ignore this email — no action will be taken.<br>
            &copy; STL City Care Clinic
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""

    async def _get_kyc_status(self, call_id: Any = None, **kwargs) -> str:
        from . import kyc_core
        try:
            cid = int(call_id) if call_id is not None else None
        except (TypeError, ValueError):
            cid = None
        st = await kyc_core.status_for_call(cid)
        return f"KYC status: {st.get('state', 'none')}" + (f" (verified: {st['name']})" if st.get("name") else "")

    async def _take_payment(self, amount: str = "0", **kwargs) -> str:
        """Confirm a simulated pre-booking payment (demo — no real money moves)."""
        amt = self._as_int(amount)
        return f"Received — your payment of {amt} rupees is confirmed."

    async def _update_symphony_ticket(self, ticket_id: str, status: str, note: str = "") -> str:
        from .summit_mcp_server import update_incident
        res = await update_incident(ticket_id, status, note)
        if res.get("status") == "SUCCESS":
            return f"Ticket {ticket_id} has been updated to {status}."
        return f"I couldn't update ticket {ticket_id} right now: {res.get('error', 'unknown error')}."

    async def _enrich_ticket_async(self, ticket_id: str, hzl_url: str) -> None:
        """Fetch real Summit details after create and update the local record + dashboard."""
        try:
            from .database import async_session
            from .models import Ticket
            from .websocket_manager import dashboard_manager
            from sqlalchemy import select
            async with httpx.AsyncClient() as client:
                auth_headers = await self._hzl_auth_headers(client, hzl_url)
                dr = await client.post(hzl_url, json={"action": "get_incident_details", "params": {"ticket_no": ticket_id}},
                                       headers=auth_headers, timeout=10.0)
                dr.raise_for_status()
                td = dr.json().get("OutputObject", {}).get("IncidentDetails", {}).get("TicketDetails", [{}])[0]
            real_priority = td.get("PriorityName")
            deadline = td.get("Resolution_Deadline")
            eng = td.get("Assigned_Engineer_Name")
            status = td.get("Status")
            async with async_session() as db:
                row = (await db.execute(select(Ticket).where(Ticket.ticket_id == ticket_id))).scalars().first()
                if row:
                    if real_priority:
                        row.priority = real_priority
                    row.transcript_summary = (row.transcript_summary or "") + f" | Summit: status={status}, engineer={eng}, SLA={deadline}"
                    await db.commit()
                    await dashboard_manager.broadcast({"type": "ticket_updated", "ticket": row.to_dict()})
        except Exception as ex:
            logger.warning(f"[Symphony enrich] {ticket_id}: {ex}")

    async def _mock_check_complaint_status(self, ticket_number: str) -> str:
        try:
            from .database import async_session
            from .models import Ticket
            from sqlalchemy import select

            query_str = str(ticket_number).strip()
            async with async_session() as db:
                stmt = select(Ticket).where(
                    (Ticket.ticket_id == query_str) |
                    (Ticket.ticket_id.contains(query_str)) |
                    (Ticket.caller_number == query_str) |
                    (Ticket.caller_number.contains(query_str))
                ).order_by(Ticket.id.desc())
                result = await db.execute(stmt)
                t = result.scalars().first()
                if t:
                    return f"Found Ticket '{t.ticket_id}' registered for '{t.caller_number}': Status is 'OPEN & IN PROGRESS'. Issue: '{t.issue}'. Priority: {t.priority}. Registered: {t.created_at.strftime('%Y-%m-%d %H:%M UTC')}."
        except Exception as e:
            logger.warning(f"[DB Ticket Check Error]: {e}")

        return f"Complaint Ticket {ticket_number} is currently 'IN PROGRESS'. Field engineer dispatched. Expected resolution by 4:00 PM today."
        
    async def _mock_transfer_to_human(self, reason: str) -> str:
        supervisor = get_config().telephony.supervisor_transfer_number
        return f"TRANSFER_HUMAN:{supervisor}"

    # ─── Sanjeevani Patient-Care Handlers ───────────────────────────────────────────────
    @staticmethod
    def _cid(call_id: Any):
        try:
            return int(call_id) if call_id is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _as_bool(v) -> bool:
        return str(v).strip().lower() in ("yes", "true", "1", "y", "correct")

    async def _sanjeevani_call_meta(self, db, call_id: Any):
        """(Call, metadata_dict) for a Sanjeevani follow-up call, or (None, {})."""
        from .models import Call
        cid = self._cid(call_id)
        if cid is None:
            return None, {}
        call = await db.get(Call, cid)
        if not call:
            return None, {}
        return call, (call.metadata_json or {})

    async def _sanjeevani_resolve_task(self, db, call_id: Any, task_id: Any = None):
        from .models import FollowUpTask
        call, meta = await self._sanjeevani_call_meta(db, call_id)
        tid = None
        try:
            tid = int(task_id) if task_id not in (None, "", "null") else None
        except (TypeError, ValueError):
            tid = None
        if tid is None:
            tid = meta.get("task_id")
        task = await db.get(FollowUpTask, int(tid)) if tid else None
        return call, meta, task

    async def _sanjeevani_identity_state(self, db, call_id: Any):
        """(call, meta) for the call, so the caller can read and update the identity flags."""
        return await self._sanjeevani_call_meta(db, call_id)

    async def _sanjeevani_identity_gate(self, call_id: Any) -> Optional[str]:
        """None when identity is confirmed on this call, otherwise the sentence to speak instead of any data."""
        from .database import async_session
        async with async_session() as db:
            call, meta = await self._sanjeevani_call_meta(db, call_id)
        if call is None:
            return IDENTITY_FIRST
        if meta.get("identity_verified"):
            return None
        return IDENTITY_FAILED if meta.get("identity_failed") else IDENTITY_FIRST

    async def _sanjeevani_confirm_identity(self, answer: str = "", call_id: Any = None, caller_number: str = "", **kwargs) -> str:
        from .models import Patient
        from .database import async_session
        if self._cid(call_id) is None:
            return "I'm sorry, I can't verify your identity on this call right now."
        async with async_session() as db:
            call, meta = await self._sanjeevani_call_meta(db, call_id)
            if call is None:
                return "I'm sorry, I can't verify your identity on this call right now."
            if meta.get("identity_verified"):
                return "Thank you, I've confirmed your identity."
            if meta.get("identity_failed"):
                return IDENTITY_FAILED
            # A call we placed knows its patient. A call the patient made is matched by the number it came from.
            patient = await db.get(Patient, int(meta["patient_id"])) if meta.get("patient_id") else None
            if patient is None:
                patient = await self._desk_patient(db, call, meta, getattr(call, "tenant_id", None), caller_number)
                if patient is not None:
                    meta = {**meta, "patient_id": patient.id}
            stored = (patient.identity_dob if patient else "") or ""
            if self._parse_dob(answer or "") is None:
                # Nothing that reads as a date (the model called the tool before the person had answered, or the answer was
                # not a date). That is not a wrong answer, so it must not use up one of the two tries.
                return "I didn't catch a date of birth. Could you tell me the date of birth, with the day, the month and the year?"
            attempts = int(meta.get("identity_attempts") or 0) + 1
            if stored and self._dob_matches(stored, answer or ""):
                call.metadata_json = {**meta, "identity_verified": True, "identity_attempts": attempts}
                await db.commit()
                return "Thank you, I've confirmed your identity."
            failed = attempts >= MAX_IDENTITY_ATTEMPTS or not stored
            call.metadata_json = {**meta, "identity_attempts": attempts, **({"identity_failed": True} if failed else {})}
            await db.commit()
        if failed:
            return IDENTITY_FAILED
        return ("That doesn't match what we have on file. Could you tell me the patient's "
                "date of birth once more?")

    _MONTHS = {
        "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6,
        "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9, "october": 10, "oct": 10,
        "november": 11, "nov": 11, "december": 12, "dec": 12,
        "يناير": 1, "فبراير": 2, "مارس": 3, "أبريل": 4, "ابريل": 4, "مايو": 5, "يونيو": 6, "يوليو": 7, "أغسطس": 8, "اغسطس": 8,
        "سبتمبر": 9, "أكتوبر": 10, "اكتوبر": 10, "نوفمبر": 11, "ديسمبر": 12,
    }

    @classmethod
    def _parse_dob(cls, text: str):
        """(day, month, year) from a date written or said in the usual ways, or None. Reads 14 March 2023, March 14th 2023,
        14/03/2023, 14-3-23, 2023-03-14 and Arabic-Indic digits. A two-digit year is kept as given (matched later)."""
        t = (text or "").lower().translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
        t = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", t)
        t = re.sub(r"\b(the|of|on|is|was|born)\b", " ", t)
        m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", t)                       # 2023-03-14
        if m:
            return int(m.group(3)), int(m.group(2)), int(m.group(1))
        m = re.search(r"(\d{1,2})[-/. ](\d{1,2})[-/. ](\d{4}|\d{2})\b", t)             # 14/03/2023, 14 3 23
        if m:
            return int(m.group(1)), int(m.group(2)), int(m.group(3))
        words = "|".join(sorted(cls._MONTHS, key=len, reverse=True))
        m = re.search(rf"(\d{{1,2}})\s*[-,]?\s*({words})\w*[\s,.-]*(\d{{4}}|\d{{2}})\b", t)    # 14 March 2023
        if m:
            return int(m.group(1)), cls._MONTHS[m.group(2)], int(m.group(3))
        m = re.search(rf"({words})\w*\s*(\d{{1,2}})[\s,.-]+(\d{{4}}|\d{{2}})\b", t)             # March 14, 2023
        if m:
            return int(m.group(2)), cls._MONTHS[m.group(1)], int(m.group(3))
        return None

    @staticmethod
    def _same_year(a: int, b: int) -> bool:
        if a == b:
            return True
        if a < 100 <= b or b < 100 <= a:                  # "23" for 2023, "99" for 1999
            short, full = (a, b) if a < 100 else (b, a)
            return full % 100 == short
        return False

    @classmethod
    def _dob_matches(cls, stored: str, answer: str) -> bool:
        """True only when the whole date matches (day, month and year), however it was said. A date that cannot be read
        never matches; day and month are swapped only when the first reading is impossible (a 14th month)."""
        want, got = cls._parse_dob(stored), cls._parse_dob(answer)
        if want is None:                                  # a stored value that is not a date: compare it as plain text
            norm = lambda s: re.sub(r"[^0-9a-z]", "", (s or "").lower())      # noqa: E731
            return bool(norm(stored)) and norm(stored) == norm(answer)
        if got is None:
            return False
        d, m, y = got
        for day, month in [(d, m)] + ([(m, d)] if m > 12 else []):
            if (day, month) == (want[0], want[1]) and cls._same_year(y, want[2]):
                return True
        return False

    async def _sanjeevani_get_prescription_context(self, task_id: Any = None, call_id: Any = None, **kwargs) -> str:
        from .models import Patient, Prescription, PrescriptionItem
        from .database import async_session
        from sqlalchemy import select
        from .followup_scheduler import build_message_context
        blocked = await self._sanjeevani_identity_gate(call_id)
        if blocked:
            return blocked
        async with async_session() as db:
            call, meta, task = await self._sanjeevani_resolve_task(db, call_id, task_id)
            pid = (task.patient_id if task else None) or meta.get("patient_id")
            prid = (task.prescription_id if task else None) or meta.get("prescription_id")
            patient = await db.get(Patient, int(pid)) if pid else None
            presc = await db.get(Prescription, int(prid)) if prid else None
            if not patient or not presc:
                return "I'm sorry, I couldn't find your prescription details for this call."
            items = (await db.execute(select(PrescriptionItem).where(
                PrescriptionItem.prescription_id == presc.id))).scalars().all()
            kind = task.kind if task else "explain"
        return build_message_context(patient, presc, items, kind)

    async def _sanjeevani_look_up(self, question: str = "", call_id: Any = None, **kwargs) -> str:
        """Answers from the company's own documents only: the search is limited to the call's company, and refused
        outright when the call has none (an unscoped search would reach every company's documents)."""
        from .database import async_session
        blocked = await self._sanjeevani_identity_gate(call_id)
        if blocked:
            return blocked
        if not (question or "").strip():
            return "What would you like me to look up?"
        async with async_session() as db:
            call, _ = await self._sanjeevani_call_meta(db, call_id)
        tenant_id = getattr(call, "tenant_id", None)
        nothing = ("I don't have that in our care information. That is a question for your doctor or pharmacist. "
                   "Shall I arrange a call or a meeting?")
        if tenant_id is None:
            return nothing
        try:
            from . import knowledge_routes
            found = await knowledge_routes.rag_engine.search(question, tenant_id=tenant_id, top_k=3)
        except Exception as exc:
            logger.warning("[Sanjeevani] knowledge look-up failed: %s", exc)
            return nothing
        if not found or not found.strip() or found.startswith("No relevant"):
            return nothing
        return found.strip()[:1500]

    async def _search_company_documents(self, question: str = "", call_id: Any = None, node_docs: Any = None, **kwargs) -> str:
        """Answers from the documents of the company the call belongs to, or only from the ones in node_docs when the agent names some.
        Refused outright when the call has no company: an unscoped search would reach every company's documents."""
        from .database import async_session
        if not (question or "").strip():
            return "What would you like me to look up?"
        async with async_session() as db:
            call, _ = await self._sanjeevani_call_meta(db, call_id)
        tenant_id = getattr(call, "tenant_id", None)
        nothing = "I don't have that in our documents. Say so, and offer to take a message so the team can follow up."
        if tenant_id is None:
            return nothing
        docs = [str(x) for x in node_docs if x] if isinstance(node_docs, (list, tuple)) else None
        try:
            from . import knowledge_routes
            found = await knowledge_routes.rag_engine.search(question, node_docs=docs or None, tenant_id=tenant_id, top_k=3)
        except Exception as exc:
            logger.warning("[Documents] look-up failed: %s", exc)
            return nothing
        if not found or not found.strip() or found.startswith("No relevant"):
            return nothing
        return found.strip()[:1800]

    async def _sanjeevani_record_understanding(self, item_id: Any = "", understood: Any = "",
                                           call_id: Any = None, **kwargs) -> str:
        from .database import async_session
        blocked = await self._sanjeevani_identity_gate(call_id)
        if blocked:
            return blocked
        async with async_session() as db:
            _, _, task = await self._sanjeevani_resolve_task(db, call_id)
            if task is not None:
                data = dict(task.outcome_json or {})
                data[f"understood_{item_id}"] = self._as_bool(understood)
                task.outcome_json = data
                await db.commit()
        return "Thank you, I've made a note of that."

    async def _sanjeevani_record_feedback(self, prescription_id: Any = "", item_id: Any = "",
                                       taken: str = "", helped: str = "", satisfaction: Any = "",
                                       side_effects: str = "", wants_doctor: Any = "",
                                       call_id: Any = None, caller_number: str = "", **kwargs) -> str:
        from .models import MedicineFeedback, Patient, Prescription
        from .database import async_session
        blocked = await self._sanjeevani_identity_gate(call_id)
        if blocked:
            return blocked
        helped_n = (helped or "").strip().lower() or None
        taken_n = (taken or "").strip().lower() or None
        sat = self._as_int(satisfaction) or None
        side = (side_effects or "").strip() or None
        wants = self._as_bool(wants_doctor)
        refill = self._as_bool(kwargs.get("refill_requested", ""))

        def _int_or_none(v):
            try:
                return int(v) if str(v).strip() not in ("", "null", "none") else None
            except (TypeError, ValueError):
                return None

        async with async_session() as db:
            call, meta = await self._sanjeevani_call_meta(db, call_id)
            tenant_id = getattr(call, "tenant_id", None)
            prid = _int_or_none(prescription_id) or meta.get("prescription_id")
            fb = MedicineFeedback(
                tenant_id=tenant_id, prescription_item_id=_int_or_none(item_id),
                prescription_id=prid, call_id=self._cid(call_id), taken=taken_n, helped=helped_n,
                satisfaction=sat, side_effects_text=side, wants_doctor=wants, refill_requested=refill,
            )
            db.add(fb)
            await db.commit()

            trigger = (helped_n in ("no", "partly")) or (sat is not None and sat <= 3) or bool(side) or wants
            if not trigger:
                await db.commit()
                return "Thank you for letting me know — I'm glad it's going well. I'll note your feedback."

            refill_only = refill and not (side or helped_n in ("no", "partly") or (sat is not None and sat <= 3))
            priority = self._sanjeevani_care_priority(helped_n, sat, side, refill_only)
            patient = await db.get(Patient, int(meta["patient_id"])) if meta.get("patient_id") else None
            presc = await db.get(Prescription, int(prid)) if prid else None
            doctor = presc.doctor_name if presc else None
            reason = side or (f"Patient not recovering (helped={helped_n})" if helped_n in ("no", "partly")
                              else f"Low satisfaction ({sat})" if sat is not None else "Refill / care follow-up")
            await self._sanjeevani_create_meeting_and_ticket(
                db, tenant_id=tenant_id, call=call, patient=patient, prescription=presc,
                doctor=doctor, reason=reason, priority=priority,
                caller_number=caller_number or (patient.phone_e164 if patient else ""))
        return ("I'm sorry to hear that. I've arranged for the clinic's care team to review this and "
                "they'll be in touch — our clinic will confirm a doctor meeting within one working day.")

    @staticmethod
    def _sanjeevani_care_priority(helped, satisfaction, side_effects, refill_only) -> str:
        if side_effects or helped == "no":
            return "high"
        if satisfaction is not None and satisfaction <= 3:
            return "normal"
        if refill_only:
            return "low"
        return "normal"

    async def _sanjeevani_create_meeting_and_ticket(self, db, *, tenant_id, call, patient, prescription,
                                                 doctor, reason, priority, caller_number,
                                                 preferred_times=None) -> str:
        from .models import DoctorMeetingRequest, Ticket
        dmr = DoctorMeetingRequest(
            tenant_id=tenant_id, patient_id=(patient.id if patient else None),
            prescription_id=(prescription.id if prescription else None),
            call_id=self._cid(call.id if call else None), doctor_name=doctor, reason=reason,
            preferred_times_json=preferred_times or None, status="requested",
        )
        db.add(dmr)
        ticket = Ticket(
            tenant_id=tenant_id, ticket_id=f"BRJ-{random.randint(100000, 999999)}",
            call_id=self._cid(call.id if call else None),
            caller_number=(caller_number or "unknown"), intent="Patient Care Follow-up",
            issue=reason or "Patient care follow-up", priority=priority,
            transcript_summary=f"Sanjeevani care follow-up: {reason}", sentiment="concern",
            created_at=datetime.utcnow(),
        )
        db.add(ticket)
        await db.commit()
        return ticket.ticket_id

    async def _sanjeevani_request_doctor_meeting(self, reason: str = "", preferred_times: str = "",
                                              call_id: Any = None, caller_number: str = "", **kwargs) -> str:
        from .models import Patient, Prescription
        from .database import async_session
        blocked = await self._sanjeevani_identity_gate(call_id)
        if blocked:
            return blocked
        async with async_session() as db:
            call, meta = await self._sanjeevani_call_meta(db, call_id)
            tenant_id = getattr(call, "tenant_id", None)
            patient = await db.get(Patient, int(meta["patient_id"])) if meta.get("patient_id") else None
            prid = meta.get("prescription_id")
            presc = await db.get(Prescription, int(prid)) if prid else None
            doctor = presc.doctor_name if presc else None
            times = [preferred_times] if preferred_times else None
            await self._sanjeevani_create_meeting_and_ticket(
                db, tenant_id=tenant_id, call=call, patient=patient, prescription=presc,
                doctor=doctor, reason=reason or "Patient requested a doctor meeting", priority="normal",
                caller_number=caller_number or (patient.phone_e164 if patient else ""),
                preferred_times=times)
        who = f"with {doctor}" if doctor else "with your doctor"
        return (f"Thank you. I've requested a meeting {who} and noted your preferred times. "
                "The clinic will confirm the appointment within one working day.")

    async def _sanjeevani_schedule_callback(self, when: str = "", call_id: Any = None, **kwargs) -> str:
        from datetime import timedelta
        from .database import async_session
        async with async_session() as db:
            _, _, task = await self._sanjeevani_resolve_task(db, call_id)
            if task is not None:
                task.status = "pending"
                task.locked_at = None
                task.next_attempt_at = None
                task.due_at = datetime.utcnow() + timedelta(days=1)
                task.outcome_json = {**(task.outcome_json or {}), "callback_requested": when}
                await db.commit()
        return "No problem — I'll arrange to call you back at a better time. Thank you."

    async def _sanjeevani_mark_do_not_call(self, call_id: Any = None, confirm: Any = "yes", **kwargs) -> str:
        from .models import Patient, FollowUpTask
        from .database import async_session
        from sqlalchemy import select
        async with async_session() as db:
            call, meta = await self._sanjeevani_call_meta(db, call_id)
            patient = await db.get(Patient, int(meta["patient_id"])) if meta.get("patient_id") else None
            if patient is not None:
                patient.do_not_call = True
                tasks = (await db.execute(select(FollowUpTask).where(
                    FollowUpTask.patient_id == patient.id,
                    FollowUpTask.status.in_(("pending", "no_answer"))))).scalars().all()
                for t in tasks:
                    t.status = "cancelled"
                    t.outcome_json = {**(t.outcome_json or {}), "cancelled_reason": "patient_do_not_call"}
                await db.commit()
        return "Understood — I've recorded that, and we won't call you again. Take care."
