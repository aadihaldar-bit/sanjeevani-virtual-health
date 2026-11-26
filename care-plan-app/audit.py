"""Audit log: who did what to which patient, and when. Append-only, in the app's own database (store.audit).

Every action that touches a patient calls log(): viewing a record, creating a care plan, pressing Call now, chatting with the
assistant. The app has one shared login, so the "actor" is the shared staff account plus a short fingerprint of the caller's address.
That tells two people apart without storing the address itself. redact() decides what from each action's details is kept, so the log
answers "what happened" without becoming a second copy of the patient's data.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request

import store

router = APIRouter(prefix="/api/v1/audit", tags=["Audit"])


# Allow-list: only these detail fields are ever written. Anything a call site adds later is dropped until it is listed here.
# Booleans and counts are safe. "kind", "alert_id" and "reason" are the app's own fixed words and ids, not patient text.
_SAFE_NUMBERS = {"emergency", "doctor", "medicines", "consent_calls", "created"}
_SAFE_WORDS = {"kind", "alert_id", "reason"}


def _mask(value: str) -> str:
    """A phone number keeps its last three digits (+9715****123); a patient id such as SNJ-98214 is not a secret and stays."""
    digits = [c for c in value if c.isdigit()]
    if value.startswith("+") and len(digits) >= 7:
        return value[:4] + "*" * (len(value) - 7) + value[-3:]
    return value


def redact(action: str, detail: Dict[str, Any]) -> Dict[str, Any]:
    """Return the part of `detail` that may be written to the audit log.

    `action` is a dotted name such as "chat.message", "alert.call_placed", "patient.viewed" or "careplan.created".
    `detail` is what the call site offered. Today it can hold: "phone" (a full +E.164 number), "question" and "reply" (free text the
    patient or the assistant wrote), "reason" (free text), "alert_id", "kind", "emergency" and "doctor" (booleans), "medicines" (a
    count), and "query" (a patient id or a phone number someone searched for).
    """
    out: Dict[str, Any] = {}
    for key, value in detail.items():
        if key in _SAFE_NUMBERS and isinstance(value, (bool, int)):
            out[key] = value
        elif key in _SAFE_WORDS and isinstance(value, str):
            out[key] = value[:120]
        elif key in ("phone", "query") and isinstance(value, str):
            out[key] = _mask(value)
        elif key in ("question", "reply") and isinstance(value, str):
            out[f"{key}_chars"] = len(value)          # how much was said, never what
    return out


def _actor(request: Optional[Request]) -> str:
    if request is None:
        return "system"
    ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "")
    return "staff:" + hashlib.sha256(ip.encode()).hexdigest()[:8] if ip else "staff"


def log(action: str, subject: Optional[str] = None, detail: Optional[Dict[str, Any]] = None, request: Optional[Request] = None) -> None:
    """Write one entry. Never raises: a failing audit write must not stop a patient getting their answer."""
    try:
        store.put_audit(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), _actor(request), action, subject,
                        redact(action, detail or {}) or None)
    except Exception:
        pass


@router.get("")
def list_entries(limit: int = 100, action: Optional[str] = None):
    return {"entries": store.list_audit(min(max(limit, 1), 500), action)}
