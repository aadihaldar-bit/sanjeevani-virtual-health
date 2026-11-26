"""The STL voicebot: follow-up calls for a care plan, and a call placed now when the patient asks for one.

Two voicebot endpoints, both with the company's API key (Authorization: Bearer stl_outbound_...):

* POST {VOICEBOT_URL}/api/v1/prescriptions saves the patient and the prescription and schedules the follow-up calls (the first
  goes through the medicines a few minutes later, a later one asks how the patient is doing). Repeat pushes of the same
  prescription id are ignored by the voicebot.
* POST {VOICEBOT_URL}/api/v1/outbound/call places one call now. The company's live agent (the Sanjeevani front desk and desks)
  answers through its outbound step, with the care plan as its brief. GET .../calls/{id}/status gives its progress, transcript
  and summary, so the page only polls: nothing on the voicebot has to reach back into this app.
* GET {VOICEBOT_URL}/api/v1/patients/record gives one patient's record held by the voice agent: calls with summaries, doctor
  appointments and pharmacy orders made by phone, follow-up calls and feedback. Shown on the app's Patient view.

Off unless VOICEBOT_URL and VOICEBOT_API_KEY are set; the page then keeps its demo assistant. Nobody is called unless they asked:
the care plan form has a tick box, and the call is a button. VOICEBOT_ALLOWED_NUMBERS (comma separated) limits who can be called,
for a preview shared on a public link. The key stays on this server; the browser only talks to /api/v1/voice."""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

import audit
import events
import store
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/voice", tags=["Voice assistant"])

MAX_BRIEF_CHARS = 3500
DONE_STATES = {"completed", "ended", "failed", "no-answer", "no_answer", "busy", "canceled", "cancelled", "error"}

_calls: Dict[str, dict] = {}


def _base() -> str:
    return os.getenv("VOICEBOT_URL", "").strip().rstrip("/")


def _key() -> str:
    return os.getenv("VOICEBOT_API_KEY", "").strip()


def configured() -> bool:
    return bool(_base() and _key())


def _headers() -> dict:
    return {"Authorization": f"Bearer {_key()}", "Content-Type": "application/json", "User-Agent": "Sanjeevani-VirtualHealth/1.0"}


def _callback() -> dict:
    """{"callback_url": ...} when the call-ended webhook is set up, else nothing."""
    url = events.callback_url()
    return {"callback_url": url} if url else {}


def number_allowed(e164: str) -> bool:
    allowed = [n.strip() for n in os.getenv("VOICEBOT_ALLOWED_NUMBERS", "").split(",") if n.strip()]
    return not allowed or e164 in allowed


def _detail(response: httpx.Response) -> str:
    try:
        d = response.json().get("detail")
        return d if isinstance(d, str) else str(d)
    except Exception:
        return response.text[:200]


# ---------------------------------------------------------------- the care plan, as the voicebot wants it
def to_voicebot_prescription(prescription_id: str, patient: dict, medicines: List[dict],
                             clinical_summary: Optional[dict], consent_calls: bool) -> dict:
    """The app's care plan in the voicebot's shape: {patient, prescription, items}."""
    summary = clinical_summary or {}
    items = []
    for m in medicines or []:
        start = m.get("start_date")
        items.append({
            "drug_name": m.get("drug_name"),
            "strength": m.get("strength"),
            "form": m.get("form"),
            "dose_text": m.get("dose_text"),
            "frequency_per_day": m.get("frequency_per_day"),
            "timing": m.get("timing"),
            "start_date": f"{start}T00:00:00" if start and len(start) == 10 else start,
            "duration_days": m.get("duration_days"),
            "plain_summary_en": m.get("instructions_plain"),
            "plain_summary_ar": m.get("instructions_arabic"),
            "status": "confirmed",
        })
    durations = [m.get("duration_days") for m in medicines or [] if isinstance(m.get("duration_days"), int)]
    return {
        "patient": {
            "external_id": patient.get("patient_id"),
            "name": patient.get("patient_name"),
            "phone_e164": patient.get("mobile_number"),
            "identity_dob": patient.get("identity_dob"),
            "preferred_language": "en",
            "consent_calls": consent_calls,
        },
        "prescription": {
            "external_id": prescription_id,
            "doctor_name": summary.get("doctor_name"),
            "facility": summary.get("hospital") or summary.get("facility"),
            "course_days": max(durations) if durations else None,
            "issued_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds"),
        },
        "items": items,
    }


def remember_plan(prescription_id: str, patient: dict, medicines: List[dict], clinical_summary: Optional[dict],
                  explanation: Optional[dict]) -> None:
    store.put_plan(prescription_id, {"patient": patient, "medicines": medicines or [], "summary": clinical_summary or {},
                                     "explanation": explanation or {}})


def get_plan(prescription_id: str) -> Optional[dict]:
    """A care plan made in the last PLAN_TTL_DAYS days, from the database, so it survives a restart."""
    return store.get_plan(prescription_id)


def care_brief(plan: dict) -> str:
    """What the agent is told about this call: who it is calling and the plan it is calling about. Facts only, from the plan."""
    p, s = plan["patient"], plan["summary"]
    lines = [
        f"You are calling {p.get('patient_name') or 'the patient'} on behalf of Sanjeevani Holdings, because they asked to talk "
        "about the care plan made after their consultation.",
        f"Doctor: {s.get('doctor_name') or 'not stated'}. Diagnosis: {s.get('primary_condition') or 'not stated'}.",
        "Prescribed medicines:",
    ]
    for m in plan["medicines"]:
        bits = [m.get("drug_name") or "medicine"]
        if m.get("strength") and m.get("strength") != "Standard":
            bits.append(m["strength"])
        bits.append(f"{m.get('dose_text') or 'as directed'}, {m.get('timing') or 'as directed'}")
        if m.get("duration_days"):
            bits.append(f"for {m['duration_days']} days")
        lines.append("- " + ", ".join(bits) + (f". {m['instructions_plain']}" if m.get("instructions_plain") else ""))
    lines.append("Answer their questions about these medicines from this list only. If they ask anything this list does not "
                 "answer, or describe a new or worsening symptom, offer to connect them to a Sanjeevani doctor.")
    return "\n".join(lines)[:MAX_BRIEF_CHARS]


# ---------------------------------------------------------------- calls to the voicebot
async def push_prescription(payload: dict) -> dict:
    """Schedule the follow-up calls. Never raises: the care plan is shown whatever happens here."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(f"{_base()}/api/v1/prescriptions", json=payload, headers=_headers())
    except Exception as exc:
        return {"status": "failed", "detail": f"the voice assistant could not be reached ({exc.__class__.__name__})"}
    if r.status_code in (200, 201):
        d = r.json()
        return {"status": "scheduled" if d.get("status") == "created" else d.get("status", "scheduled"),
                "tasks_created": d.get("tasks_created", 0), "voicebot_prescription_id": d.get("prescription_id")}
    if r.status_code in (401, 403):
        return {"status": "failed", "detail": "the voice assistant refused this app's key"}
    return {"status": "failed", "detail": _detail(r)}


class CallMeRequest(BaseModel):
    prescription_id: str
    phone_number: Optional[str] = None


class BookAppointmentCallRequest(BaseModel):
    doctor_name: str
    doctor_title: Optional[str] = None
    hospital: Optional[str] = None
    slot: Optional[str] = None
    patient_id: Optional[str] = "SNJ-98214"
    patient_name: Optional[str] = "Sonika"
    phone_number: Optional[str] = None
    reason: Optional[str] = None


class BookAppointmentDirectRequest(BaseModel):
    doctor_name: str
    specialty: Optional[str] = None
    booked_slot: Optional[str] = None
    patient_id: Optional[str] = "SNJ-98214"
    patient_name: Optional[str] = "Sonika"
    phone_number: Optional[str] = "+919380361678"
    reason: Optional[str] = "Consultation"
    fee_amount: Optional[float] = 400.0
    copay_amount: Optional[float] = 400.0


class PatientUpsertRequest(BaseModel):
    external_id: Optional[str] = None
    phone_e164: str
    first_name: str
    last_name: Optional[str] = None
    identity_dob: Optional[str] = None
    preferred_language: Optional[str] = "en"
    consent_calls: Optional[bool] = True


@router.get("/status")
def voice_status():
    return {"configured": configured()}


_health_cache: Dict[str, Any] = {"at": 0.0, "body": None}


@router.get("/health")
async def voice_health():
    """Is the voice assistant there, and does it accept this app's key? Cached for 15 s so the page can ask often."""
    if not configured():
        return {"state": "not_configured", "detail": "VOICEBOT_URL or VOICEBOT_API_KEY is not set"}
    if _health_cache["body"] and time.time() - _health_cache["at"] < 15:
        return _health_cache["body"]
    body: Dict[str, Any] = {"state": "ok", "reachable": False, "database": None, "key_ok": None, "latency_ms": None}
    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            h = await client.get(f"{_base()}/health")
            body["latency_ms"] = round((time.time() - t0) * 1000)
            body["reachable"] = h.status_code == 200
            if body["reachable"]:
                body["database"] = (h.json() or {}).get("database")
                k = await client.get(f"{_base()}/api/v1/patients", headers=_headers())
                body["key_ok"] = k.status_code == 200
    except Exception as exc:
        body["detail"] = f"could not reach the voice assistant ({exc.__class__.__name__})"
    if not body["reachable"]:
        body["state"] = "down"
    elif body["key_ok"] is False:
        body["state"], body["detail"] = "degraded", "the voice assistant refused this app's key"
    elif body["database"] not in ("connected", None):
        body["state"], body["detail"] = "degraded", f"the voice assistant's database is {body['database']}"
    elif (body["latency_ms"] or 0) > 4000:
        body["state"], body["detail"] = "degraded", "the voice assistant is answering slowly"
    _health_cache.update(at=time.time(), body=body)
    return body


@router.post("/call-me")
async def call_me(body: CallMeRequest, request: Request):
    if not configured():
        raise HTTPException(503, "the voice assistant is not connected to this app")
    plan = get_plan(body.prescription_id)
    if not plan:
        raise HTTPException(404, "this care plan has expired; create it again to get a call")
    phone = (body.phone_number or plan["patient"].get("mobile_number") or "").strip()
    if not phone:
        raise HTTPException(422, "no phone number for this patient")
    if not number_allowed(phone):
        raise HTTPException(403, "calls from this preview only go to the demo numbers")
    name = plan["patient"].get("patient_name") or None
    first = (name or "").split()[0] if name else ""
    payload = {
        "phone_number": phone,
        "recipient_name": name,
        "message_context": care_brief(plan),
        "greeting_template": (f"Hello {first}, this is the Sanjeevani care assistant. " if first else
                              "Hello, this is the Sanjeevani care assistant. ")
                             + "You asked us to call about your new medicines. Is now a good time?",
        "system_prompt": "Keep each reply short, one question at a time. Never change a dose or add a medicine.",
        "metadata": {"source": "sanjeevani-virtual-health", "prescription_id": body.prescription_id},
        **_callback(),
    }
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(f"{_base()}/api/v1/outbound/call", json=payload, headers=_headers())
    except Exception as exc:
        raise HTTPException(502, f"the voice assistant could not be reached ({exc.__class__.__name__})")
    if r.status_code not in (200, 201, 202):
        raise HTTPException(502 if r.status_code >= 500 else r.status_code, _detail(r))
    d = r.json()
    call_id = str(d.get("call_id"))
    _calls[call_id] = {"prescription_id": body.prescription_id, "phone": phone, "at": time.time()}
    audit.log("call.placed", body.prescription_id, {"phone": phone, "kind": "care_plan"}, request)
    return {"call_id": call_id, "phone_number": phone, "message": d.get("message")}


@router.post("/book-appointment-call")
async def book_appointment_call(body: BookAppointmentCallRequest):
    if not configured():
        raise HTTPException(503, "the voice assistant is not connected to this app")
    phone = (body.phone_number or "+919380361678").strip()
    if not number_allowed(phone):
        raise HTTPException(403, "calls from this preview only go to the demo numbers")
    name = (body.patient_name or "Sonika").strip()
    first = name.split()[0] if name else "there"
    doc = body.doctor_name
    slot = body.slot or "the next opening"
    hosp = body.hospital or "Sanjeevani Hospital"
    payload = {
        "phone_number": phone,
        "recipient_name": name,
        "message_context": (
            f"You are calling patient {name} (MRN: {body.patient_id or 'SNJ-98214'}) on behalf of Sanjeevani Holdings Appointment Desk "
            f"to schedule a doctor consultation with {doc} ({body.doctor_title or 'Specialist'} at {hosp}). "
            f"Offered slot: {slot}. Confirm their preferred slot, ask about any symptoms or reason for visit, "
            f"and let them know their consultation fee is payable at the clinic visit."
        ),
        "greeting_template": (
            f"Hello {first}, this is the Sanjeevani Hospitals appointment desk calling to help you schedule your consultation with {doc}. "
            f"Is this a good time to confirm your slot?"
        ),
        "system_prompt": (
            f"You are the Sanjeevani Appointment Desk voice agent. You are calling {name} (MRN: {body.patient_id or 'SNJ-98214'}) "
            f"to book an appointment with {doc}. Be polite and helpful. Offer the slot {slot} and answer any questions."
        ),
        "metadata": {
            "source": "sanjeevani-virtual-health",
            "action": "book_appointment",
            "patient_id": body.patient_id or "SNJ-98214",
            "doctor_name": doc,
            "slot": slot
        },
        **_callback(),
    }
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(f"{_base()}/api/v1/outbound/call", json=payload, headers=_headers())
    except Exception as exc:
        raise HTTPException(502, f"the voice assistant could not be reached ({exc.__class__.__name__})")
    if r.status_code not in (200, 201, 202):
        raise HTTPException(502 if r.status_code >= 500 else r.status_code, _detail(r))
    d = r.json()
    call_id = str(d.get("call_id"))
    _calls[call_id] = {"kind": "appointment_booking", "doctor_name": doc, "phone": phone, "at": time.time()}
    return {"call_id": call_id, "phone_number": phone, "message": d.get("message")}


@router.post("/book-appointment-direct")
async def book_appointment_direct(body: BookAppointmentDirectRequest):
    if not configured():
        raise HTTPException(503, "the voice assistant is not connected to this app")
    phone = (body.phone_number or "+919380361678").strip()
    payload = {
        "external_id": body.patient_id or "SNJ-98214",
        "phone": phone,
        "patient_name": body.patient_name or "Sonika",
        "doctor_name": body.doctor_name,
        "specialty": body.specialty,
        "booked_slot": body.booked_slot or "Next available slot",
        "reason": body.reason or "Doctor consultation",
        "fee_amount": body.fee_amount or 400.0,
        "copay_amount": body.copay_amount or 400.0
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(f"{_base()}/api/v1/patients/appointments", json=payload, headers=_headers())
    except Exception as exc:
        raise HTTPException(502, f"the voice assistant could not be reached ({exc.__class__.__name__})")
    if r.status_code != 200:
        raise HTTPException(502 if r.status_code >= 500 else r.status_code, _detail(r))
    return r.json()



class AppointmentUpdateRequest(BaseModel):
    status: str
    booked_slot: Optional[str] = None


@router.get("/appointments")
async def list_appointments(source: Optional[str] = None, status: Optional[str] = None):
    """The company's doctor appointments: those patients booked by phoning in (inbound) and those the assistant called about."""
    if not configured():
        raise HTTPException(503, "the voice assistant is not connected to this app")
    params = {k: v for k, v in (("source", source), ("status", status)) if v}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(f"{_base()}/api/v1/patients/appointments", params=params, headers=_headers())
    except Exception as exc:
        raise HTTPException(502, f"the voice assistant could not be reached ({exc.__class__.__name__})")
    if r.status_code in (404, 405):
        raise HTTPException(501, "the voice assistant has not been updated to list appointments yet")
    if r.status_code != 200:
        raise HTTPException(502 if r.status_code >= 500 else r.status_code, _detail(r))
    return r.json()


@router.get("/call-transcript/{call_id}")
async def call_transcript(call_id: int):
    """What was said on one past call (voicebot /api/v1/patients/calls/{id}/transcript)."""
    if not configured():
        raise HTTPException(503, "the voice assistant is not connected to this app")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(f"{_base()}/api/v1/patients/calls/{call_id}/transcript", headers=_headers())
    except Exception as exc:
        raise HTTPException(502, f"the voice assistant could not be reached ({exc.__class__.__name__})")
    if r.status_code == 404 and "no call" not in (r.text or ""):
        raise HTTPException(501, "the voice assistant has not been updated to share transcripts yet")
    if r.status_code != 200:
        raise HTTPException(502 if r.status_code >= 500 else r.status_code, _detail(r))
    return r.json()


@router.put("/appointments/{appointment_id}")
async def update_appointment(appointment_id: int, body: AppointmentUpdateRequest):
    """Book, complete or cancel an appointment. Completing it schedules the patient's feedback call."""
    if not configured():
        raise HTTPException(503, "the voice assistant is not connected to this app")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.put(f"{_base()}/api/v1/patients/appointments/{appointment_id}", json=body.dict(exclude_none=True),
                                 headers=_headers())
    except Exception as exc:
        raise HTTPException(502, f"the voice assistant could not be reached ({exc.__class__.__name__})")
    if r.status_code != 200:
        raise HTTPException(502 if r.status_code >= 500 else r.status_code, _detail(r))
    return r.json()


@router.get("/patients")
async def list_voicebot_patients():
    """Fetch all patients registered under Sanjeevani Holdings from Voicebot."""
    if not configured():
        raise HTTPException(503, "the voice assistant is not connected to this app")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(f"{_base()}/api/v1/patients", headers=_headers())
    except Exception as exc:
        raise HTTPException(502, f"voice assistant could not be reached ({exc.__class__.__name__})")
    if r.status_code != 200:
        raise HTTPException(502 if r.status_code >= 500 else r.status_code, _detail(r))
    return r.json()


@router.post("/patients/upsert")
async def upsert_voicebot_patient(body: PatientUpsertRequest):
    """Upsert patient into Voicebot directory."""
    if not configured():
        raise HTTPException(503, "the voice assistant is not connected to this app")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(f"{_base()}/api/v1/patients/upsert", json=body.dict(), headers=_headers())
    except Exception as exc:
        raise HTTPException(502, f"voice assistant could not be reached ({exc.__class__.__name__})")
    if r.status_code != 200:
        raise HTTPException(502 if r.status_code >= 500 else r.status_code, _detail(r))
    return r.json()


@router.get("/patient-record")
async def patient_record(request: Request, patient_id: Optional[str] = None, phone: Optional[str] = None):
    """What the voice agent knows about one patient: calls, appointments, orders, follow-ups (voicebot /api/v1/patients/record)."""
    if not configured():
        raise HTTPException(503, "the voice assistant is not connected to this app")
    params = {k: v.strip() for k, v in (("external_id", patient_id), ("phone", phone)) if v and v.strip()}
    if not params:
        raise HTTPException(422, "give a patient ID or a phone number")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(f"{_base()}/api/v1/patients/record", params=params, headers=_headers())
    except Exception as exc:
        raise HTTPException(502, f"the voice assistant could not be reached ({exc.__class__.__name__})")
    if r.status_code == 404:
        raise HTTPException(404, "the voice assistant has no record of this patient yet")
    if r.status_code != 200:
        raise HTTPException(502 if r.status_code >= 500 else r.status_code, _detail(r))
    audit.log("patient.viewed", patient_id or phone, {"query": patient_id or phone}, request)
    return r.json()


@router.get("/calls/{call_id}")
async def call_progress(call_id: str):
    if call_id not in _calls:
        raise HTTPException(404, "unknown call")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{_base()}/api/v1/outbound/calls/{call_id}/status", headers=_headers())
    except Exception:
        return {"call_id": call_id, "stage": "unknown", "done": False, "transcript": []}
    if r.status_code != 200:
        return {"call_id": call_id, "stage": "unknown", "done": False, "transcript": [], "detail": _detail(r)}
    d = r.json()
    status, live = (d.get("status") or "").lower(), (d.get("live_status") or "").lower()
    done = bool(d.get("ended_at")) or status in DONE_STATES or live in DONE_STATES
    # The voicebot's live_status runs queued -> initiated -> ringing -> in-progress -> completed (or cancelled).
    stage = ("ended" if done else "in_call" if live in ("in-progress", "in_progress")
             else "ringing" if live in ("ringing", "initiated") else "calling")
    transcript = []
    for t in d.get("transcript") or []:
        if not isinstance(t, dict):
            continue
        who = str(t.get("role") or t.get("speaker") or "").lower()          # the voicebot writes role "user" / "agent"
        text = t.get("message") or t.get("text") or t.get("content") or ""
        if text:
            transcript.append({"from": "agent" if who in ("agent", "assistant", "bot", "ai") else "patient", "text": text})
    return {"call_id": call_id, "stage": stage, "done": done, "status": d.get("status"), "live_status": d.get("live_status"),
            "duration_seconds": d.get("duration_seconds"), "summary": d.get("summary"), "transcript": transcript}
