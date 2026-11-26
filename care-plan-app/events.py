"""Call-ended webhook from the voice assistant, and a small feed the page polls so it can refresh the moment something happens.

The voicebot posts a call summary to a callback_url when an outbound call ends: once, with no signature and no retry. So this
receiver is a hint, not the only source: it authenticates with a secret in the URL (CALLBACK_SECRET), keeps only a small event
(never the transcript or the summary text) and the page keeps its slow polling as the safety net.

To turn it on, set CALLBACK_SECRET and APP_PUBLIC_URL on this app, and SANJEEVANI_CALLBACK_URL on the voicebot for the follow-up
calls it places itself: <APP_PUBLIC_URL>/api/v1/voice/callback/<CALLBACK_SECRET>.
"""
from __future__ import annotations

import hmac
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request

import audit
import store

router = APIRouter(tags=["Events"])

MAX_BODY = 2_000_000


def callback_url() -> Optional[str]:
    """Where the voicebot should post when a call this app places ends, or None when the webhook is not set up."""
    base, secret = os.getenv("APP_PUBLIC_URL", "").strip().rstrip("/"), os.getenv("CALLBACK_SECRET", "").strip()
    return f"{base}/api/v1/voice/callback/{secret}" if base and secret else None


def _small(payload: Dict[str, Any]) -> Dict[str, Any]:
    """The part of a call summary worth keeping as an event: ids and yes/no facts, no words from the call."""
    telephony = payload.get("telephony") or {}
    stats = payload.get("telephony_stats") or {}
    return {"has_appointment": bool(payload.get("appointment")),
            "seconds": int(stats.get("duration_seconds") or telephony.get("duration_seconds") or 0),
            "provider": str(telephony.get("provider") or "")[:20]}


@router.post("/api/v1/voice/callback/{token}")
async def call_ended(token: str, request: Request):
    secret = os.getenv("CALLBACK_SECRET", "").strip()
    if not secret or not hmac.compare_digest(token, secret):
        raise HTTPException(404, "not found")                       # the same answer for "off" and "wrong", so it can't be probed
    if int(request.headers.get("content-length") or 0) > MAX_BODY:
        raise HTTPException(413, "payload too large")
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(422, "expected a JSON call summary")
    if not isinstance(payload, dict):
        raise HTTPException(422, "expected a JSON call summary")
    ref = str(payload.get("call_id") or "")
    detail = _small(payload)
    store.put_event("call_ended", ref, detail)
    audit.log("call.ended", ref or None, {"seconds": detail["seconds"], "created": int(detail["has_appointment"])}, None)
    return {"ok": True}


@router.get("/api/v1/events")
def feed(since: int = 0):
    """Events newer than `since`. since=-1 returns nothing but the newest id, which is how the page starts without a flood."""
    last = store.last_event_id()
    return {"events": [] if since < 0 else store.list_events(since), "last": last}
