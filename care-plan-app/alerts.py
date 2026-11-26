"""Care alerts: a refill running out, or a worrying vitals estimate, queues an alert for staff. Nothing dials by itself.

A person reviews each alert and presses Call now. Before the voice assistant is asked to ring, the server checks that the patient
agreed to calls and is not on the do-not-call list, that the number is on VOICEBOT_ALLOWED_NUMBERS (when set), that it is not
quiet hours in Dubai, and that the patient has not already had ALERT_MAX_CALLS_PER_DAY alert calls today. The same patient and
reason is queued once a day. The call brief tells the assistant never to diagnose or change a medicine, and to send anyone who
sounds unwell to emergency care.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import audit
import store
import voicebot

router = APIRouter(prefix="/api/v1/alerts", tags=["Alerts"])

DUBAI = ZoneInfo("Asia/Dubai")
QUIET_FROM, QUIET_TO = int(os.getenv("ALERT_QUIET_FROM", "21")), int(os.getenv("ALERT_QUIET_TO", "8"))
MAX_CALLS_PER_DAY = int(os.getenv("ALERT_MAX_CALLS_PER_DAY", "2"))
REFILL_DAYS_AHEAD = int(os.getenv("ALERT_REFILL_DAYS", "3"))
# A resting estimate this far out, and read with good confidence, is worth a check-in call. Never a diagnosis.
HR_HIGH, HR_LOW, RR_HIGH, RR_LOW = 120, 45, 26, 8

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _quiet_now() -> bool:
    h = datetime.now(DUBAI).hour
    return (h >= QUIET_FROM or h < QUIET_TO) if QUIET_FROM > QUIET_TO else (QUIET_FROM <= h < QUIET_TO)


def _add(kind: str, patient: dict, title: str, detail: str, key: str, data: dict) -> Optional[dict]:
    """Queue an alert unless the same one was raised for this patient in the last 24 hours."""
    if store.recent_alert_exists(key, _iso(_now() - timedelta(hours=24))):
        return None
    a = {"id": uuid.uuid4().hex[:8], "kind": kind, "patient": patient, "title": title, "detail": detail, "key": key,
         "data": data, "status": "queued", "created_at": _iso(_now()), "call_id": None, "reason": ""}
    store.put_alert(a)
    return a


# ---------------------------------------------------------------- the voicebot's view of a patient
async def _vb_get(client: httpx.AsyncClient, path: str, params: Optional[dict] = None) -> Any:
    r = await client.get(f"{voicebot._base()}{path}", params=params, headers=voicebot._headers())
    if r.status_code != 200:
        raise HTTPException(502 if r.status_code >= 500 else r.status_code, voicebot._detail(r))
    return r.json()


async def _patient_flags(client: httpx.AsyncClient, ext: Optional[str], phone: Optional[str]) -> dict:
    """Whether this patient agreed to calls, from the voicebot's own directory."""
    d = await _vb_get(client, "/api/v1/patients")
    for p in d.get("patients", []):
        if (ext and p.get("external_id") == ext) or (phone and p.get("phone_e164") == phone):
            return {"found": True, "consent": bool(p.get("consent_calls")) and not p.get("do_not_call"), "phone": p.get("phone_e164"),
                    "name": p.get("name"), "external_id": p.get("external_id")}
    return {"found": False, "consent": False, "phone": phone, "name": None, "external_id": ext}


# ---------------------------------------------------------------- raising alerts
def _parse(iso: Optional[str]) -> Optional[datetime]:
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def refill_alerts_for(patient: dict, record: dict) -> List[dict]:
    """Medicines whose written course ends within REFILL_DAYS_AHEAD days and that have no pharmacy order since."""
    out: List[dict] = []
    orders_text = " ".join(json.dumps(o.get("items") or o.get("requested") or "").lower() for o in record.get("orders", []))
    today = _now()
    for rx in record.get("prescriptions", []):
        issued = _parse(rx.get("issued_at"))
        if not issued:
            continue
        due = []
        for it in rx.get("items", []):
            days = it.get("duration_days")
            name = (it.get("drug_name") or "").strip()
            if not name or not isinstance(days, (int, float)) or days <= 0:
                continue
            ends = issued + timedelta(days=days)
            left = (ends - today).total_seconds() / 86400
            if -14 <= left <= REFILL_DAYS_AHEAD and name.lower().split()[0] not in orders_text:
                due.append((name, ends, left))
        if due:
            names = ", ".join(n for n, _, _ in due)
            soonest = min(e for _, e, _ in due).astimezone(DUBAI).strftime("%d %b")
            a = _add("refill", patient, f"Refill due: {names}",
                     f"The course ends around {soonest} and no pharmacy order has been placed.",
                     f"refill|{patient.get('external_id') or patient.get('phone')}|{rx.get('id')}",
                     {"drugs": [n for n, _, _ in due], "ends": soonest, "prescription": rx.get("external_id")})
            if a:
                out.append(a)
    return out


@router.post("/scan")
async def scan_refills(request: Request):
    """Look through the patients who agreed to calls for medicines that are about to run out."""
    if not voicebot.configured():
        raise HTTPException(503, "the voice assistant is not connected to this app")
    created: List[dict] = []
    checked = skipped = 0
    async with httpx.AsyncClient(timeout=20.0) as client:
        patients = (await _vb_get(client, "/api/v1/patients")).get("patients", [])
        for p in patients:
            if not p.get("consent_calls") or p.get("do_not_call"):
                skipped += 1
                continue
            params = {"external_id": p["external_id"]} if p.get("external_id") else {"phone": p.get("phone_e164")}
            try:
                record = await _vb_get(client, "/api/v1/patients/record", params)
            except HTTPException:
                continue
            checked += 1
            created += refill_alerts_for({"name": p.get("name"), "external_id": p.get("external_id"), "phone": p.get("phone_e164")}, record)
    audit.log("alert.scan", detail={"created": len(created)}, request=request)
    return {"checked": checked, "skipped_no_consent": skipped, "created": len(created)}


class VitalsReport(BaseModel):
    patient_id: Optional[str] = None
    phone: Optional[str] = None
    name: Optional[str] = None
    hr: Optional[float] = None
    hr_confidence: Optional[str] = None
    rr: Optional[float] = None
    rr_confidence: Optional[str] = None


@router.post("/vitals")
async def vitals_report(body: VitalsReport):
    """Queue a check-in alert when a well-read estimate is far from usual. Approximate or low-confidence readings never raise one."""
    reasons = []
    if body.hr is not None and body.hr_confidence == "good" and (body.hr >= HR_HIGH or body.hr <= HR_LOW):
        reasons.append(f"resting heart rate about {round(body.hr)} bpm")
    if body.rr is not None and body.rr_confidence == "good" and (body.rr >= RR_HIGH or body.rr <= RR_LOW):
        reasons.append(f"breathing about {round(body.rr)} breaths a minute")
    if not reasons:
        return {"alert": None}
    patient = {"name": body.name, "external_id": body.patient_id, "phone": body.phone}
    a = _add("vitals", patient, "Vitals check: worth a call", "A video check estimated " + " and ".join(reasons) + ". It is an estimate, not a diagnosis.",
             f"vitals|{body.patient_id or body.phone}", {"hr": body.hr, "rr": body.rr})
    return {"alert": a}


# ---------------------------------------------------------------- reviewing and calling
def _public(a: dict) -> dict:
    return a


@router.get("")
def list_alerts():
    rows = store.list_alerts()
    return {"alerts": rows, "rules": {"quiet_hours": f"{QUIET_TO:02d}:00 to {QUIET_FROM:02d}:00 Dubai", "quiet_now": _quiet_now(),
            "max_calls_per_day": MAX_CALLS_PER_DAY, "connected": voicebot.configured(),
            "allow_list": bool(os.getenv("VOICEBOT_ALLOWED_NUMBERS", "").strip())}}


@router.post("/{alert_id}/dismiss")
def dismiss(alert_id: str, request: Request):
    a = store.get_alert(alert_id)
    if not a:
        raise HTTPException(404, "unknown alert")
    a["status"] = "dismissed"
    store.put_alert(a)
    audit.log("alert.dismissed", a["patient"].get("external_id") or a["patient"].get("phone"), {"alert_id": a["id"], "kind": a["kind"]}, request)
    return a


def _brief(a: dict) -> dict:
    name = a["patient"].get("name") or "the patient"
    first = name.split()[0] if name != "the patient" else ""
    hello = f"Hello {first}, this is the Sanjeevani care assistant. " if first else "Hello, this is the Sanjeevani care assistant. "
    if a["kind"] == "refill":
        drugs = ", ".join(a["data"].get("drugs", []))
        context = (f"Patient {name}. The supply of {drugs} runs out around {a['data'].get('ends')} and no refill has been ordered. "
                   "Ask if they need a refill and offer to send it for home delivery or pickup. Ask if they have had any problems with the medicine.")
        greeting = hello + "I am calling because your medicine is about to run out. Is now a good time?"
    else:
        context = (f"Patient {name}. A video wellness check estimated: {a['detail']} Ask how they are feeling and whether they have any "
                   "symptoms. Offer to book a doctor visit. Do not give a diagnosis.")
        greeting = hello + "I am calling to see how you are feeling after your recent check. Is now a good time?"
    system = ("Keep each reply short, one question at a time. Never diagnose, never change a dose or add a medicine. If the patient "
              "mentions chest pain, trouble breathing, fainting or feeling very unwell, tell them to call emergency services "
              "(998 in the UAE) or go to the nearest emergency room now.")
    return {"message_context": context, "greeting_template": greeting, "system_prompt": system}


@router.post("/{alert_id}/call")
async def call_now(alert_id: str, request: Request):
    a = store.get_alert(alert_id)
    if not a:
        raise HTTPException(404, "unknown alert")
    if a["status"] not in ("queued", "blocked"):
        raise HTTPException(409, "this alert has already been handled")
    if not voicebot.configured():
        raise HTTPException(503, "the voice assistant is not connected to this app")

    async with httpx.AsyncClient(timeout=20.0) as client:
        flags = await _patient_flags(client, a["patient"].get("external_id"), a["patient"].get("phone"))
        phone = flags.get("phone") or a["patient"].get("phone")
        problem = None
        if not flags["found"] or not flags["consent"]:
            problem = "the patient has not agreed to calls"
        elif not phone:
            problem = "there is no phone number for this patient"
        elif not voicebot.number_allowed(phone):
            problem = "calls from this preview only go to the demo numbers"
        elif _quiet_now():
            problem = f"it is quiet hours in Dubai (calls from {QUIET_TO:02d}:00 to {QUIET_FROM:02d}:00)"
        else:
            today = datetime.now(DUBAI).date()
            made = sum(1 for x in store.list_alerts() if x.get("call_id") and x["patient"].get("external_id") == a["patient"].get("external_id")
                       and (_parse(x.get("called_at")) or _now()).astimezone(DUBAI).date() == today)
            if made >= MAX_CALLS_PER_DAY:
                problem = f"this patient has already had {made} alert calls today"
        if problem:
            a["status"], a["reason"] = "blocked", problem
            store.put_alert(a)
            audit.log("alert.call_blocked", a["patient"].get("external_id") or phone, {"alert_id": a["id"], "kind": a["kind"], "reason": problem}, request)
            raise HTTPException(409, problem)

        payload = {"phone_number": phone, "recipient_name": a["patient"].get("name"), **_brief(a),
                   "metadata": {"source": "sanjeevani-virtual-health", "alert": a["kind"], "alert_id": a["id"]}, **voicebot._callback()}
        try:
            r = await client.post(f"{voicebot._base()}/api/v1/outbound/call", json=payload, headers=voicebot._headers())
        except Exception as exc:
            raise HTTPException(502, f"the voice assistant could not be reached ({exc.__class__.__name__})")
    if r.status_code not in (200, 201, 202):
        raise HTTPException(502 if r.status_code >= 500 else r.status_code, voicebot._detail(r))
    call_id = str(r.json().get("call_id"))
    voicebot._calls[call_id] = {"kind": f"alert_{a['kind']}", "phone": phone, "at": time.time()}
    a["status"], a["call_id"], a["called_at"], a["reason"] = "called", call_id, _iso(_now()), ""
    store.put_alert(a)
    audit.log("alert.call_placed", a["patient"].get("external_id") or phone, {"alert_id": a["id"], "kind": a["kind"], "phone": phone}, request)
    return {"call_id": call_id, "phone_number": phone, "alert": a}
