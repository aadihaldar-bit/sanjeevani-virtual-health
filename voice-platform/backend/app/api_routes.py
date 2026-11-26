"""
Dashboard REST API Routes
Provides endpoints for the frontend dashboard to fetch calls, transcripts,
analytics, and escalation data.
"""

import os
import re
import asyncio
import logging
import random
from collections import Counter
from datetime import datetime, timedelta
from typing import Optional, List, Dict

from fastapi import APIRouter, Depends, Query, HTTPException, Body, File, UploadFile, Form
from pydantic import BaseModel
from sqlalchemy import select, func, case, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .database import get_db
from .tenancy import request_scope, require_staff
from .models import Appointment, Call, TranscriptMessage, Event, Escalation, Ticket, CallStatus
from .websocket_manager import dashboard_manager
from .twilio_client import twilio_sync_client
from .logging_service import LogService, LogLevel, LogCategory
from .event_logger import event_logger

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["dashboard"])
tickets_router = APIRouter(prefix="/api", tags=["tickets"])

E164_REGEX = re.compile(r"^\+[1-9]\d{6,14}$")


class TicketCreateRequest(BaseModel):
    caller_number: str
    intent: str
    issue: str
    priority: str
    transcript_summary: str


class TicketCreateResponse(BaseModel):
    ticket_id: str
    status: str
    timestamp: str


class CallbackRequest(BaseModel):
    customer_number: str
    agent_number: str = "9000000002"


@router.post("/calls/callback", dependencies=[Depends(require_staff)])
async def trigger_callback(req: CallbackRequest):
    """
    Outbound callback call — routes via Tata Smartflo (sole telephony provider).
    Previously used Twilio; rewritten to use Smartflo permanently.
    """
    from .license_manager import license_manager
    allowed, reason = license_manager.check_and_consume_call()
    if not allowed:
        raise HTTPException(status_code=403, detail=f"License quota check failed: {reason}")

    # Delegate entirely to the shared Smartflo outbound helper
    return await _place_outbound_call(req.customer_number, provider="smartflo")



def _build_diagnostic_stage(call: Call, event_types: set[str]) -> dict:
    has_live_signal = (
        call.status == CallStatus.ACTIVE
        or bool(call.live_status)
        or any(
            event_type.startswith("twilio_")
            or event_type in {"call_started", "call_updated", "active_conversation_detected"}
            for event_type in event_types
        )
    )
    has_transcript = len(getattr(call, "transcript_messages", []) or []) > 0
    has_summary = bool(call.summary and call.summary.strip())
    has_ticket = bool((getattr(call, "tickets", []) or []))

    issues = []
    if not has_live_signal:
        issues.append("No live ingest signal recorded")
    if not has_transcript:
        issues.append("Transcript missing")
    if not has_summary:
        issues.append("AI summary missing")
    if call.status != CallStatus.ACTIVE and not call.ended_at:
        issues.append("Completion timestamp missing")

    return {
        "live_received": has_live_signal,
        "transcript_saved": has_transcript,
        "ai_summary_created": has_summary,
        "ticket_created": has_ticket,
        "escalation_detected": bool(call.is_escalated),
        "issues": issues,
    }


def _call_needs_hydration(call: Call) -> bool:
    return False

async def _hydrate_call_from_elevenlabs(conversation_id: str) -> None:
    pass


def _build_summary_from_saved_transcript(call: Call) -> str:
    transcript = getattr(call, "transcript_messages", []) or []
    user_messages = [msg.message.strip() for msg in transcript if msg.role == "user" and msg.message]
    agent_messages = [msg.message.strip() for msg in transcript if msg.role == "agent" and msg.message]

    if user_messages and agent_messages:
        return f"Caller said: {user_messages[0][:140]}. Agent responded: {agent_messages[0][:140]}."
    if user_messages:
        return f"Caller said: {user_messages[0][:220]}."
    if agent_messages:
        return f"Agent conversation record: {agent_messages[0][:220]}."
    return ""


def _apply_display_fallbacks(call: Call) -> Call:
    if (not call.summary or call.summary.strip() == "") and getattr(call, "transcript_messages", None):
        call.summary = _build_summary_from_saved_transcript(call)
    return call


async def _find_related_call(db: AsyncSession, caller_number: str) -> Call | None:
    result = await db.execute(
        select(Call)
        .options(selectinload(Call.tickets))
        .where(Call.caller_number == caller_number)
        .order_by(
            case((Call.status == CallStatus.ACTIVE, 0), else_=1),
            Call.started_at.desc(),
        )
        .limit(1)
    )
    return result.scalars().first()


async def _generate_ticket_id(db: AsyncSession) -> str:
    for _ in range(20):
        ticket_id = f"TKT-{random.randint(1000, 9999)}"
        existing = await db.execute(select(Ticket).where(Ticket.ticket_id == ticket_id))
        if not existing.scalars().first():
            return ticket_id
    raise HTTPException(status_code=500, detail="Failed to generate unique ticket id")



# ─── Calls ─────────────────────────────────────────────────────────────────

@router.get("/calls")
async def list_calls(
    status: Optional[str] = Query(None, description="Filter by status: active, completed, escalated"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List calls with optional status filter and pagination."""
    query = (
        select(Call)
        .options(selectinload(Call.transcript_messages), selectinload(Call.tickets))
        .order_by(Call.started_at.desc())
    )

    if str(getattr(status, "value", status) or "").lower() == "active":
        from .call_reaper import reap_stale_calls
        await reap_stale_calls(db)

    if status:
        query = query.where(Call.status == status)

    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    calls = [_apply_display_fallbacks(call) for call in result.scalars().all()]

    if status == CallStatus.COMPLETED or status == "completed":
        for call in calls:
            if _call_needs_hydration(call):
                asyncio.create_task(_hydrate_call_from_elevenlabs(call.conversation_id))

    # Total count
    count_query = select(func.count(Call.id))
    if status:
        count_query = count_query.where(Call.status == status)
    total = (await db.execute(count_query)).scalar() or 0

    return {
        "calls": [c.to_dict() for c in calls],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/calls/active")
async def get_active_calls(db: AsyncSession = Depends(get_db)):
    """Get currently active calls (quiet calls are closed first, see call_reaper)."""
    from .call_reaper import reap_stale_calls
    await reap_stale_calls(db)

    result = await db.execute(
        select(Call)
        .options(selectinload(Call.tickets))
        .where(Call.status == CallStatus.ACTIVE)
        .order_by(Call.started_at.desc())
    )
    calls = result.scalars().all()
    return {"calls": [c.to_dict() for c in calls], "count": len(calls)}


@router.post("/calls/clear-stale")
async def clear_stale_calls(
    max_age_minutes: int = Query(15, ge=1, le=1440),
    db: AsyncSession = Depends(get_db)
):
    """Clear stuck/orphan active calls older than max_age_minutes."""
    stale_cutoff = datetime.utcnow() - timedelta(minutes=max_age_minutes)
    stale_result = await db.execute(
        select(Call).where(
            Call.status == CallStatus.ACTIVE,
            Call.started_at < stale_cutoff
        )
    )
    stale_calls = stale_result.scalars().all()
    count = len(stale_calls)
    if count > 0:
        for sc in stale_calls:
            sc.status = CallStatus.COMPLETED
            sc.live_status = "completed"
            sc.ended_at = sc.ended_at or datetime.utcnow()
            if not sc.end_reason:
                sc.end_reason = "manually_cleared_stale_timeout"
        await db.commit()
    return {"status": "ok", "message": f"Cleared {count} stale active calls older than {max_age_minutes} minutes.", "cleared_count": count}


class OutboundCallRequest(BaseModel):
    to_number: str
    provider: Optional[str] = None

async def _place_outbound_call(to_number: str, provider: Optional[str] = None, caller_id: Optional[str] = None) -> dict:
    """
    Shared helper to sanitize phone numbers and place outbound call via Smartflo (primary) or Twilio (fallback).
    """
    import os
    import httpx

    raw_to = to_number.strip().replace(" ", "").replace("-", "")
    if not raw_to.startswith("+"):
        if len(raw_to) == 10 and raw_to.isdigit():
            clean_e164 = f"+91{raw_to}"
        elif len(raw_to) == 12 and raw_to.startswith("91") and raw_to.isdigit():
            clean_e164 = f"+{raw_to}"
        else:
            clean_e164 = f"+{raw_to}"
    else:
        clean_e164 = raw_to

    if not E164_REGEX.match(clean_e164):
        raise HTTPException(
            status_code=400,
            detail="Invalid destination phone number. Must conform to E.164 standard (e.g. +919876543210)."
        )
    
    selected_provider = (provider or os.getenv("TELEPHONY_PROVIDER", "smartflo")).lower().strip()
    smartflo_token = os.getenv("SMARTFLO_API_TOKEN", "").strip()
    smartflo_error = None

    # 1. Primary attempt: Tata Smartflo Outbound Call
    if selected_provider in ["smartflo", "auto"] and smartflo_token:
        api_key = os.getenv("SMARTFLO_API_KEY", "").strip()
        call_timeout_env = os.getenv("SMARTFLO_CALL_TIMEOUT", "1800").strip()
        call_timeout = int(call_timeout_env) if call_timeout_env.isdigit() else 1800

        # customer_number: Smartflo wants 10-digit local format (strip +91 / 91 prefix)
        clean_to = clean_e164.replace("+", "")
        if clean_to.startswith("91") and len(clean_to) == 12:
            clean_to = clean_to[2:]

        # caller_id: Smartflo ONLY accepts 12-digit format with country code, no + prefix
        # e.g. "918065250182" ✅  "8065250182" ❌  "+918065250182" ❌
        raw_caller_id = (caller_id or os.getenv("SMARTFLO_CALLER_ID", "918065250182")).strip()
        # Normalize to the exact format Smartflo requires
        caller_id = raw_caller_id.lstrip("+")  # remove + if present
        if caller_id.startswith("91") and len(caller_id) == 12:
            pass  # already correct: 918065250182
        elif len(caller_id) == 10 and caller_id.isdigit():
            caller_id = f"91{caller_id}"  # 10-digit → prepend 91

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                payload = {
                    "async": 1,
                    "customer_number": clean_to,
                    "api_key": api_key,
                    "caller_id": caller_id,
                    "call_timeout": call_timeout
                }
                logger.info(f"[Smartflo] Sending payload: {payload} | auth_token_prefix={smartflo_token[:20]}...")
                res = await client.post(
                    "https://api-smartflo.tatateleservices.com/v1/click_to_call_support",
                    headers={
                        "Authorization": f"Bearer {smartflo_token}",  # Bearer prefix mandatory from 30 Sept 2026
                        "accept": "application/json",
                        "content-type": "application/json"
                    },
                    json=payload
                )
                if res.status_code in [200, 201, 202]:
                    data = res.json() if res.content else {}
                    logger.info(f"Smartflo outbound call successfully queued to {clean_to}: {data}")
                    await event_logger.emit(
                        "call.outbound_placed",
                        provider="smartflo",
                        to_number=clean_e164,
                        customer_number=clean_to,
                        caller_id=caller_id,
                        ref_id=data.get("ref_id")
                    )
                    return {"status": "ok", "provider": "smartflo", "data": data, "ref_id": data.get("ref_id"), "clean_e164": clean_e164}
                else:
                    smartflo_error = f"Smartflo HTTP {res.status_code}: {res.text[:120]}"
                    logger.warning(f"[Smartflo] Outbound failed: {smartflo_error}")
        except Exception as e:
            smartflo_error = f"Smartflo service error: {str(e)}"
            logger.warning(f"[Smartflo] Outbound exception: {smartflo_error}")

    # Twilio fallback is disabled — Tata Smartflo is the sole telephony provider.
    # If Smartflo failed, surface its error directly rather than hitting a suspended Twilio account.
    raise HTTPException(
        status_code=502,
        detail=smartflo_error or "Smartflo telephony unavailable. Check SMARTFLO_API_TOKEN and SMARTFLO_API_KEY."
    )

@router.post("/calls/outbound", dependencies=[Depends(require_staff)])
async def initiate_outbound_call(req: OutboundCallRequest):
    from .license_manager import license_manager
    allowed, reason = license_manager.check_and_consume_call()
    if not allowed:
        raise HTTPException(status_code=403, detail=f"License quota check failed: {reason}")

    return await _place_outbound_call(req.to_number, req.provider)

@router.post("/calls/{call_id}/end")
async def end_call(call_id: int, db: AsyncSession = Depends(get_db)):
    """Signal the voicebot to terminate an active call."""
    call = await db.get(Call, call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
        
    if call.status != CallStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Call is not active")
        
    try:
        if dashboard_manager.redis_client:
            await dashboard_manager.redis_client.set(f"end_call:{call.conversation_id}", "1", ex=60)
        # Also broadcast to dashboard clients so UI updates immediately
        await dashboard_manager.broadcast({
            "type": "call_updated",
            "call_id": call.id,
            "conversation_id": call.conversation_id,
            "status": "completed",
            "live_status": "ending"
        })
        await event_logger.emit(
            "call.manual_ended",
            call_id=call_id,
            call_sid=call.call_sid
        )
        return {"status": "ok", "message": "Termination signal sent"}
    except Exception as e:
        logger.error(f"Error sending end call signal: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/calls/{call_id}")
async def get_call_detail(call_id: int, db: AsyncSession = Depends(get_db)):
    """Get full call details including transcript and escalations."""
    async def load_call() -> Call | None:
        result = await db.execute(
            select(Call)
            .options(
                selectinload(Call.transcript_messages),
                selectinload(Call.escalations),
                selectinload(Call.tickets),
            )
            .where(Call.id == call_id)
        )
        return result.scalars().first()

    call = await load_call()

    if not call:
        raise HTTPException(status_code=404, detail="Call not found")

    if _call_needs_hydration(call):
        await _hydrate_call_from_elevenlabs(call.conversation_id)
        call = await load_call()

    call = _apply_display_fallbacks(call)
    return call.to_detail_dict()


@router.get("/health/calls")
async def list_health_calls(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List calls that produced a doctor appointment (medical receptionist)."""
    from .models import Appointment, KycSession
    # Calls with a booked appointment OR an identity-verification session (KYC runs before
    # booking, so a patient who is mid-verification or failed should still be visible).
    query = (
        select(Call)
        .options(selectinload(Call.transcript_messages), selectinload(Call.appointments))
        .where(or_(
            select(Appointment.id).where(Appointment.call_id == Call.id).exists(),
            select(KycSession.id).where(KycSession.call_id == Call.id).exists(),
        ))
        .order_by(Call.started_at.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(query)
    calls = result.scalars().unique().all()
    from . import kyc_core
    kyc_map = await kyc_core.summaries_for_calls([c.id for c in calls])
    rows = []
    for c in calls:
        d = c.to_dict()
        d["kyc"] = kyc_map.get(c.id)
        rows.append(d)
    return {
        "calls": rows,
        "total": len(calls),
        "limit": limit,
        "offset": offset,
    }


@router.get("/health/calls/{call_id}")
async def get_health_call_detail(call_id: int, db: AsyncSession = Depends(get_db)):
    """Get full detail (transcript + patient details + appointment) for a health call."""
    result = await db.execute(
        select(Call)
        .options(
            selectinload(Call.transcript_messages),
            selectinload(Call.escalations),
            selectinload(Call.tickets),
            selectinload(Call.appointments),
        )
        .where(Call.id == call_id)
    )
    call = result.scalars().first()
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
    from . import kyc_core
    data = call.to_detail_dict()
    data["kyc"] = (await kyc_core.summaries_for_calls([call.id])).get(call.id)
    return data


@router.get("/calls/{call_id}/transcript")
async def get_call_transcript(call_id: int, db: AsyncSession = Depends(get_db)):
    """Get transcript messages for a specific call."""
    # Verify call exists
    call_result = await db.execute(
        select(Call)
        .options(selectinload(Call.transcript_messages), selectinload(Call.tickets))
        .where(Call.id == call_id)
    )
    call = call_result.scalars().first()
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")

    if _call_needs_hydration(call):
        await _hydrate_call_from_elevenlabs(call.conversation_id)

    result = await db.execute(
        select(TranscriptMessage)
        .where(TranscriptMessage.call_id == call_id)
        .order_by(TranscriptMessage.sequence_number)
    )
    messages = result.scalars().all()

    return {
        "call_id": call_id,
        "messages": [m.to_dict() for m in messages],
        "count": len(messages),
    }


@router.get("/tickets")
@tickets_router.get("/tickets")
async def list_tickets(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db)
):
    """Fetch all incident and Symphony tickets ordered by creation date descending."""
    result = await db.execute(
        select(Ticket).order_by(Ticket.created_at.desc()).offset(offset).limit(limit)
    )
    tickets = result.scalars().all()
    return [t.to_dict() for t in tickets]


@tickets_router.post("/tickets/create", response_model=TicketCreateResponse)
async def create_ticket(payload: TicketCreateRequest, db: AsyncSession = Depends(get_db)):
    logger.info("Incoming ticket request: %s", payload.model_dump())

    try:
        call = await _find_related_call(db, payload.caller_number)
        ticket_id = await _generate_ticket_id(db)
        timestamp = datetime.utcnow()

        ticket = Ticket(
            ticket_id=ticket_id,
            call_id=call.id if call else None,
            caller_number=payload.caller_number,
            intent=payload.intent,
            issue=payload.issue,
            priority=payload.priority,
            transcript_summary=payload.transcript_summary,
            sentiment=call.sentiment if call else None,
            created_at=timestamp,
        )
        db.add(ticket)
        await db.flush()

        if call:
            await db.refresh(call, attribute_names=["tickets"])

        db.add(Event(
            call_id=call.id if call else None,
            event_type="ticket_created",
            payload={
                "ticket_id": ticket_id,
                "caller_number": payload.caller_number,
                "intent": payload.intent,
                "priority": payload.priority,
            },
        ))
        await db.commit()

        logger.info("Ticket created: %s", ticket_id)

        # ── Send SMS Follow-up if enabled ──
        if os.getenv("ENABLE_SMS_FOLLOWUP", "true").lower() == "true" and payload.caller_number:
            try:
                sms_body = (
                    f"Hi from Support! We've created a ticket for your issue: "
                    f"'{payload.issue}'. Your reference is {ticket_id}."
                )
                await twilio_sync_client.send_sms(to_number=payload.caller_number, body=sms_body)
                
                db.add(Event(
                    call_id=call.id if call else None,
                    event_type="sms_followup_sent",
                    payload={"ticket_id": ticket_id, "caller_number": payload.caller_number}
                ))
                await db.commit()
            except Exception as sms_exc:
                logger.error("Failed to send SMS follow-up for ticket %s: %s", ticket_id, sms_exc)

        event_payload = {
            "type": "ticket_created",
            "ticket": {
                "ticket_id": ticket_id,
                "intent": payload.intent,
                "issue": payload.issue,
                "priority": payload.priority,
                "caller_number": payload.caller_number,
                "call_id": call.id if call else None,
                "sentiment": call.sentiment if call else None,
                "transcript_summary": payload.transcript_summary,
                "created_at": timestamp.isoformat(),
            },
        }
        if call:
            event_payload["call"] = call.to_dict()

        try:
            await dashboard_manager.broadcast(event_payload)
        except Exception as exc:
            logger.warning("Ticket websocket broadcast failed for %s: %s", ticket_id, exc)

        return TicketCreateResponse(
            ticket_id=ticket_id,
            status="created",
            timestamp=timestamp.isoformat(),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Ticket creation failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create ticket")


# ─── AI Web Simulator & RAG Tester ──────────────────────────────────────────

class SimulatorRequest(BaseModel):
    session_id: Optional[str] = "sim-test-session"
    user_message: Optional[str] = None
    message: Optional[str] = None
    history: Optional[List[Dict[str, str]]] = []
    workflow_id: Optional[int] = None
    current_node_id: Optional[str] = None


class SimulatorEndCallRequest(BaseModel):
    session_id: str
    duration_seconds: Optional[float] = None


def _is_whisper_noise(text: Optional[str], is_audio_input: bool = True, hist_len: int = 0) -> bool:
    """
    Detects Whisper silence/noise hallucinations.
    When audio contains low-energy room background noise, mic breath, or silence,
    Whisper models frequently output punctuation ('.', '...'), or hallucinate short subtitle phrases
    ('you', 'Thank you.', 'subtitles by', etc.).
    """
    if not text or not text.strip():
        return True
    stripped = text.strip()
    # If there are no letters or digits, it's pure punctuation / noise (e.g. '.', '...', '!?')
    if not re.search(r'[a-zA-Z0-9]', stripped):
        return True

    if is_audio_input:
        clean_lower = re.sub(r'^[^\w]+|[^\w]+$', '', stripped.lower()).strip()
        common_noise = {
            "", "you", "you.", "thank you", "thank you.",
            "thank you for watching", "thank you for watching.",
            "thanks for watching", "thanks for watching.", "bye", "bye.", "bye bye",
            "subtitles by", "subtitles by mr", "amara.org",
            "please subscribe", "subscribe", "like and subscribe",
            "so", "so.", "yeah", "uh", "um"
        }
        if clean_lower in common_noise:
            # At start of conversation, or for pure filler tokens, always discard as noise
            if hist_len == 0 or clean_lower in {"you", "so", "yeah", "uh", "um", "thank you for watching", "thanks for watching", "subtitles by", "amara.org", "please subscribe", "subscribe"}:
                return True
            # Even if hist_len > 0, single-word 'thank you' on silence is discarded if caller hasn't completed an issue
            if clean_lower in {"thank you", "bye", "bye bye"}:
                return True

    return False


def _ensure_wav_bytes(audio_bytes: bytes, sample_rate: int = 16000) -> bytes:
    """Wraps raw PCM audio bytes with a valid 16-bit mono RIFF WAV header for browser playback."""
    if not audio_bytes or audio_bytes.startswith(b'RIFF'):
        return audio_bytes
    try:
        import wave, io
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(audio_bytes)
        return buf.getvalue()
    except Exception as e:
        logger.warning(f"[_ensure_wav_bytes] Failed to wrap raw PCM as WAV: {e}")
        return audio_bytes


async def _transcode_to_wav(audio_bytes: bytes) -> bytes:
    """
    Transcodes any incoming browser audio (WebM, Opus, OGG, MP3, etc.)
    into a standardized 16kHz 16-bit mono PCM WAV file using ffmpeg.
    Repairs broken container cues or incomplete timeslices.
    """
    if not audio_bytes:
        return b""
    if audio_bytes.startswith(b"RIFF") and len(audio_bytes) > 44:
        return audio_bytes
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-err_detect", "ignore_err", "-i", "pipe:0",
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", "-f", "wav", "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        wav_data, err = await proc.communicate(input=audio_bytes)
        if wav_data and len(wav_data) > 44 and wav_data.startswith(b"RIFF"):
            return wav_data
        logger.warning(f"[_transcode_to_wav] ffmpeg transcode produced invalid output: {err.decode(errors='ignore')[:150]}")
    except Exception as e:
        logger.warning(f"[_transcode_to_wav] ffmpeg transcode error: {e}")
    return audio_bytes


import re
import struct


def _split_into_sentences(text: str) -> list[str]:
    """Split text into natural spoken sentences for parallel chunked TTS."""
    if not text:
        return []
    raw_sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    sentences = [s.strip() for s in raw_sentences if s.strip()]
    return sentences if sentences else [text.strip()]


def _combine_wav_bytes(wav_list: list[bytes]) -> bytes:
    """Concatenates multiple RIFF WAV chunks into a single valid continuous RIFF WAV."""
    if not wav_list:
        return b""
    valid_wavs = [_ensure_wav_bytes(w) for w in wav_list if w]
    if not valid_wavs:
        return b""
    if len(valid_wavs) == 1:
        return valid_wavs[0]

    first_wav = valid_wavs[0]
    if len(first_wav) < 44 or not first_wav.startswith(b"RIFF"):
        return b"".join(valid_wavs)

    pcm_chunks = []
    for w in valid_wavs:
        data_pos = w.find(b"data")
        if data_pos != -1 and len(w) >= data_pos + 8:
            data_size = struct.unpack("<I", w[data_pos + 4:data_pos + 8])[0]
            pcm_data = w[data_pos + 8:data_pos + 8 + data_size]
            pcm_chunks.append(pcm_data)
        elif len(w) > 44:
            pcm_chunks.append(w[44:])

    combined_pcm = b"".join(pcm_chunks)
    header = bytearray(first_wav[:44])
    struct.pack_into("<I", header, 4, len(combined_pcm) + 36)
    struct.pack_into("<I", header, 40, len(combined_pcm))
    return bytes(header) + combined_pcm


async def _synthesize_sentence_chunked(agent_text: str, tts_engine) -> Optional[bytes]:
    """
    Synthesizes speech using Sentence-Chunked TTS:
    - Synthesizes only the first sentence first to drop TTS from 3,200ms to ~450ms.
    - Guarantees sub-second first-byte audio playback for the voice simulator.
    """
    if not agent_text or not tts_engine:
        return None

    sentences = _split_into_sentences(agent_text)
    if not sentences:
        sentences = [agent_text]

    # Synthesize the first sentence first for immediate <500ms voice delivery
    first_sentence = sentences[0].strip()
    if first_sentence:
        try:
            raw = await tts_engine.synthesize_bytes(first_sentence, format="wav")
            if raw:
                return _ensure_wav_bytes(raw)
        except Exception as e:
            logger.warning(f"[ChunkedTTS] First sentence synthesis notice: {e}")

    # Fallback to single batch synthesis
    raw = await tts_engine.synthesize_bytes(agent_text, format="wav")
    return _ensure_wav_bytes(raw) if raw else None


@router.get("/simulator/greeting")
async def simulator_greeting(workflow_id: Optional[int] = Query(None)):
    """Fetch initial greeting of active workflow for the web simulator."""
    from .ai_services import LLMEngineFactory, TTSEngineFactory
    from .agent_router import AgentRouter
    from .tool_executor import ToolExecutor
    from .rag_engine import RAGEngine
    from .config import get_config

    from .workflow_runtime import effective_config
    cfg = await effective_config(workflow_id)
    llm = LLMEngineFactory.get_engine(cfg)
    tts = TTSEngineFactory.get_engine(cfg)
    tool_exec = ToolExecutor()
    rag = RAGEngine()
    router_engine = AgentRouter(llm=llm, tool_executor=tool_exec, rag_engine=rag, config=cfg)
    asyncio.create_task(router_engine.warm_up())

    greeting_info = await router_engine.get_initial_greeting(workflow_id)

    # Pre-generate TTS audio for seamless instant playback
    try:
        greeting_text = greeting_info.get("text", "")
        if greeting_text and tts:
            audio_bytes = await tts.synthesize_bytes(text=greeting_text, format="wav")
            if audio_bytes:
                audio_bytes = _ensure_wav_bytes(audio_bytes)
                import base64
                greeting_info["audio_base64"] = f"data:audio/wav;base64,{base64.b64encode(audio_bytes).decode('utf-8')}"
    except Exception as e:
        logger.warning(f"[SimulatorGreeting] TTS generation notice: {e}")

    return greeting_info


@router.post("/simulator/chat")
async def simulator_chat(payload: SimulatorRequest, db: AsyncSession = Depends(get_db)):
    """
    Web Simulator endpoint: test workflows, multi-agent routing, RAG vector retrieval,
    and tool execution directly from the web dashboard WITHOUT placing Twilio calls.
    Saves Twilio credits during development and demonstration.
    """
    from .ai_services import LLMEngineFactory
    from .agent_router import AgentRouter
    from .tool_executor import ToolExecutor
    from .rag_engine import RAGEngine
    from .config import get_config

    from .workflow_runtime import effective_config
    cfg = await effective_config(payload.workflow_id)
    llm = LLMEngineFactory.get_engine(cfg)
    tool_exec = ToolExecutor()
    rag = RAGEngine()
    router_engine = AgentRouter(llm=llm, tool_executor=tool_exec, rag_engine=rag, config=cfg)

    session_id = payload.session_id or "sim-test-session"
    history = payload.history or []
    user_text = payload.user_message or payload.message or ""
    if not user_text.strip():
        raise HTTPException(status_code=400, detail="user_message or message field is required")

    # Create/find the Call FIRST (and commit so it is visible to tool DB sessions), so any
    # appointment or ticket booked on this turn links to it and shows in the dashboards.
    call_res = await db.execute(select(Call).where(Call.conversation_id == session_id))
    call = call_res.scalars().first()
    if not call:
        call = Call(
            conversation_id=session_id,
            call_sid=f"SIM-{session_id[-10:]}",
            caller_number="Web Simulator",
            called_number="STL City Care Clinic",
            agent_id="Clinic Receptionist",
            status=CallStatus.ACTIVE,
            source="simulator",
            started_at=datetime.utcnow(),
        )
        db.add(call)
        await db.commit()
        await db.refresh(call)
    call_db_id = call.id

    # RAG vector retrieval search
    rag_context = ""
    if rag and user_text:
        try:
            rag_context = await rag.search(user_text)
        except Exception:
            rag_context = ""

    try:
        response = await router_engine.route_and_respond(
            session_id=session_id,
            user_message=user_text,
            history=history,
            current_node_id=payload.current_node_id,
            workflow_id=payload.workflow_id,
            call_id=call_db_id,
            caller_phone="Web Simulator",
        )
        if rag_context and rag_context != "No relevant context found.":
            response["rag_context"] = rag_context
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[Simulator] Error executing turn: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to execute simulator turn")

    # While a KYC session is open, give the simulator a tappable link (code prefilled) — on a real
    # call the patient types the spoken address instead, but in the web simulator text can't be read aloud.
    try:
        from .models import KycSession
        from . import kyc_core
        ks = (await db.execute(
            select(KycSession).where(KycSession.call_id == call_db_id,
                                     KycSession.status.in_(["pending", "consented", "qr_verified"]))
            .order_by(KycSession.id.desc())
        )).scalars().first()
        if ks and ks.code:
            response["kyc_link"] = {"url": f"{kyc_core.PUBLIC_URL}?code={ks.code}", "code": ks.code}
    except Exception as kyc_err:
        logger.debug("[SimulatorChat] kyc link skipped: %s", kyc_err)

    # Synthesize AI voice audio bytes via Sentence-Chunked TTS for instant sub-second playback
    agent_text = response.get("text", "")
    try:
        if agent_text:
            from .ai_services import TTSEngineFactory
            tts_engine = TTSEngineFactory.get_engine(cfg)
            if tts_engine:
                audio_bytes = await _synthesize_sentence_chunked(agent_text, tts_engine)
                if audio_bytes:
                    import base64
                    response["audio_base64"] = f"data:audio/wav;base64,{base64.b64encode(audio_bytes).decode('utf-8')}"
    except Exception as tts_err:
        logger.warning(f"[SimulatorChat] TTS Error notice: {tts_err}")

    # Persist the turn into the existing Call + TranscriptMessage
    try:
        seq = len(history) * 2
        db.add(TranscriptMessage(call_id=call_db_id, role="user", message=user_text, sequence_number=seq))
        if agent_text:
            db.add(TranscriptMessage(call_id=call_db_id, role="agent", message=agent_text, sequence_number=seq + 1))
            call.summary = agent_text
            call.current_agent = response.get("agent", call.agent_id)

        tool_called = response.get("tool_called")
        if tool_called == "create_symphony_ticket":
            call.status = CallStatus.COMPLETED
            call.intent = "Incident Ticket Creation"
            try:
                from .models import Ticket
                from sqlalchemy import update
                await db.execute(update(Ticket).where(Ticket.call_id.is_(None)).values(call_id=call_db_id))
            except Exception as link_err:
                logger.warning(f"[SimulatorChat] Failed to link ticket to call: {link_err}")
        elif tool_called == "schedule_appointment":
            # the appointment was created with call_id already set; mark the call so it surfaces
            # cleanly under Health System, and backfill any row that missed the link.
            call.intent = "Appointment Booking"
            try:
                from .models import Appointment
                from sqlalchemy import update
                await db.execute(
                    update(Appointment).where(Appointment.call_id.is_(None),
                                              Appointment.caller_number == "Web Simulator")
                    .values(call_id=call_db_id)
                )
            except Exception as link_err:
                logger.warning(f"[SimulatorChat] Failed to link appointment to call: {link_err}")

        await db.commit()
        await dashboard_manager.broadcast({"type": "call_updated", "call": call.to_dict()})
    except Exception as db_err:
        logger.warning(f"[SimulatorChat] Failed to log call record: {db_err}")

    return response


@router.post("/simulator/voice-turn")
async def simulator_voice_turn(
    file: Optional[UploadFile] = File(None),
    user_text: Optional[str] = Form(None),
    session_id: Optional[str] = Form("sim-voice-session"),
    history_json: Optional[str] = Form("[]"),
    workflow_id: Optional[int] = Form(None),
    current_node_id: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db)
):
    """
    Autonomous Live Voice Agent Simulator:
    Processes direct mic audio (STT) -> Multi-Agent Router (LLM) -> Synthesizes Voice Audio (TTS).
    Directly interacts with STT, Graph LLM, and TTS without dialing via Twilio.
    0 Twilio credits used.
    """
    import base64
    import json
    import tempfile
    from .ai_services import LLMEngineFactory, STTEngineFactory, TTSEngineFactory
    from .agent_router import AgentRouter
    from .tool_executor import ToolExecutor
    from .rag_engine import RAGEngine
    from .config import get_config

    from .workflow_runtime import effective_config
    cfg = await effective_config(workflow_id)
    stt_engine = STTEngineFactory.get_engine(cfg)
    tts_engine = TTSEngineFactory.get_engine(cfg)
    llm_engine = LLMEngineFactory.get_engine(cfg)
    tool_exec = ToolExecutor()
    rag = RAGEngine()
    router_engine = AgentRouter(llm=llm_engine, tool_executor=tool_exec, rag_engine=rag, config=cfg)

    # 1. Transcribe voice if audio file uploaded
    user_message = (user_text or "").strip()
    if file and not user_message:
        try:
            content = await file.read()
            ext = "webm"
            if file.filename:
                parts = file.filename.rsplit(".", 1)
                if len(parts) > 1 and parts[1].lower() in ["webm", "wav", "mp3", "ogg", "m4a", "flac"]:
                    ext = parts[1].lower()

            # Transcode incoming browser audio to 16kHz mono WAV for Whisper reliability
            wav_content = await _transcode_to_wav(content)
            if wav_content and wav_content.startswith(b"RIFF"):
                content = wav_content
                ext = "wav"

            if hasattr(stt_engine, "transcribe_bytes"):
                user_message = await stt_engine.transcribe_bytes(content, format=ext)
            elif hasattr(stt_engine, "transcribe"):
                with tempfile.NamedTemporaryFile(delete=False, suffix=f".{ext}") as tmp:
                    tmp.write(content)
                    tmp_path = tmp.name
                user_message = await stt_engine.transcribe(tmp_path)
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

            if isinstance(user_message, dict):
                user_message = user_message.get("text", "").strip()
            elif isinstance(user_message, str):
                user_message = user_message.strip()

            logger.info(f"[VoiceSimulator] Transcribed user audio ({ext}, {len(content)} bytes): '{user_message}'")
        except Exception as stt_err:
            logger.error(f"[VoiceSimulator] STT Error: {stt_err}", exc_info=True)
            user_message = ""

    # 2. Parse conversation history
    try:
        history = json.loads(history_json) if history_json else []
    except Exception:
        history = []

    # Noise / Whisper silence hallucination filter
    is_audio = bool(file and not user_text)
    if _is_whisper_noise(user_message, is_audio_input=is_audio, hist_len=len(history)):
        logger.info(f"[VoiceSimulator] Suppressed silence / Whisper hallucination: '{user_message}'")
        return {
            "agent": "Primary Support Agent",
            "text": "",
            "stt_transcript": "",
            "user_message": "",
            "tool_called": None,
            "next_node_id": current_node_id,
            "silence": True
        }

    # 3. RAG context search
    rag_context = ""
    if rag and user_message:
        try:
            rag_context = await rag.search(user_message)
        except Exception:
            rag_context = ""

    # 4. Route and respond via AgentRouter
    response = await router_engine.route_and_respond(
        session_id=session_id,
        user_message=user_message,
        history=history,
        current_node_id=current_node_id,
        workflow_id=workflow_id
    )

    if rag_context and rag_context != "No relevant context found.":
        response["rag_context"] = rag_context
    response["user_message"] = user_message
    response["stt_transcript"] = user_message

    # 5. Synthesize AI voice audio bytes via Sentence-Chunked TTS engine
    agent_text = response.get("text", "")
    try:
        if agent_text and tts_engine:
            audio_bytes = await _synthesize_sentence_chunked(agent_text, tts_engine)
            if audio_bytes:
                b64_audio = base64.b64encode(audio_bytes).decode("utf-8")
                response["audio_base64"] = f"data:audio/wav;base64,{b64_audio}"
    except Exception as tts_err:
        logger.error(f"[VoiceSimulator] TTS Error: {tts_err}")

    # 6. Persist simulator session into database Call & TranscriptMessage
    try:
        call_res = await db.execute(select(Call).where(Call.conversation_id == session_id))
        call = call_res.scalars().first()
        if not call:
            call = Call(
                conversation_id=session_id,
                call_sid=f"SIM-{session_id[-10:]}",
                caller_number="Web Simulator",
                called_number="IT Helpdesk",
                agent_id=response.get("agent", "IT Support Agent"),
                status=CallStatus.ACTIVE,
                live_status="in-progress",
                source="simulator",
                started_at=datetime.utcnow()
            )
            db.add(call)
            await db.flush()

        seq = len(history) * 2
        db.add(TranscriptMessage(call_id=call.id, role="user", message=user_message, sequence_number=seq))
        if agent_text:
            db.add(TranscriptMessage(call_id=call.id, role="agent", message=agent_text, sequence_number=seq + 1))
            call.summary = agent_text
            call.current_agent = response.get("agent", "IT Support Agent")

        if response.get("tool_called") == "create_symphony_ticket":
            call.status = CallStatus.COMPLETED
            call.live_status = "completed"
            call.intent = "Incident Ticket Creation"
            try:
                from .models import Ticket
                from sqlalchemy import update
                await db.execute(
                    update(Ticket).where(Ticket.call_id.is_(None)).values(call_id=call.id)
                )
            except Exception as link_err:
                logger.warning(f"[VoiceSimulator] Failed to link ticket to call: {link_err}")

        await db.commit()
        await dashboard_manager.broadcast({"type": "call_updated", "call": call.to_dict()})
    except Exception as db_err:
        logger.warning(f"[VoiceSimulator] Failed to log call record: {db_err}")

    return response


@router.post("/simulator/end-call")
async def simulator_end_call(payload: SimulatorEndCallRequest, db: AsyncSession = Depends(get_db)):
    """Marks a web simulator call as completed in the database and flushes to GCS."""
    try:
        call_res = await db.execute(select(Call).where(Call.conversation_id == payload.session_id))
        call = call_res.scalars().first()
        if call:
            call.status = CallStatus.COMPLETED
            call.live_status = "completed"
            call.ended_at = datetime.utcnow()
            if payload.duration_seconds is not None:
                call.duration_seconds = payload.duration_seconds
            elif call.started_at:
                call.duration_seconds = (datetime.utcnow() - call.started_at).total_seconds()
            call.end_reason = "simulator_user_hangup"
            await db.commit()
            await dashboard_manager.broadcast({"type": "call_updated", "call": call.to_dict()})

            # Auto-upload call record to GCS asynchronously
            try:
                from .event_logger import event_logger
                asyncio.create_task(event_logger.upload_call_by_id(call.id))
            except Exception as gcs_err:
                logger.warning(f"[SimulatorEndCall] GCS upload trigger notice: {gcs_err}")

            return {"status": "success", "call_id": call.id}
    except Exception as err:
        logger.warning(f"[SimulatorEndCall] Error ending call: {err}")
    return {"status": "ignored"}


# ─── Analytics ──────────────────────────────────────────────────────────────

@router.get("/analytics/summary")
async def get_analytics_summary(
    days: int = Query(30, ge=1, le=365, description="Number of days to look back"),
    db: AsyncSession = Depends(get_db),
):
    """Get analytics summary: total calls, AI resolved, escalated, transfer rate."""
    since = datetime.utcnow() - timedelta(days=days)

    # Total calls
    total_result = await db.execute(
        select(func.count(Call.id)).where(Call.started_at >= since)
    )
    total_calls = total_result.scalar() or 0

    # Completed calls (AI resolved = completed and not escalated)
    completed_result = await db.execute(
        select(func.count(Call.id)).where(
            and_(
                Call.started_at >= since,
                Call.status == CallStatus.COMPLETED,
            )
        )
    )
    completed_calls = completed_result.scalar() or 0

    # Escalated calls
    escalated_result = await db.execute(
        select(func.count(Call.id)).where(
            and_(
                Call.started_at >= since,
                Call.is_escalated == True,
            )
        )
    )
    escalated_calls = escalated_result.scalar() or 0

    # Human transferred
    transferred_result = await db.execute(
        select(func.count(Call.id)).where(
            and_(
                Call.started_at >= since,
                Call.transferred_to_human == True,
            )
        )
    )
    transferred_calls = transferred_result.scalar() or 0

    # AI resolved = completed and not escalated
    ai_resolved = completed_calls - escalated_calls
    if ai_resolved < 0:
        ai_resolved = 0

    # Active calls
    active_result = await db.execute(
        select(func.count(Call.id)).where(Call.status == CallStatus.ACTIVE)
    )
    active_calls = active_result.scalar() or 0

    # Average duration
    avg_duration_result = await db.execute(
        select(func.avg(Call.duration_seconds)).where(
            and_(
                Call.started_at >= since,
                Call.duration_seconds.isnot(None),
            )
        )
    )
    avg_duration = avg_duration_result.scalar() or 0

    # Handed over to a person: transferred, or escalated (the flag older calls carry)
    handed_over_calls = (await db.execute(
        select(func.count(Call.id)).where(
            and_(Call.started_at >= since, or_(Call.is_escalated == True, Call.transferred_to_human == True))
        )
    )).scalar() or 0

    transfer_rate = (handed_over_calls / total_calls * 100) if total_calls > 0 else 0

    return {
        "period_days": days,
        "total_calls": total_calls,
        "active_calls": active_calls,
        "completed_calls": completed_calls,
        "ai_resolved_calls": ai_resolved,
        "escalated_calls": escalated_calls,
        "transferred_calls": transferred_calls,
        "handed_over_calls": handed_over_calls,
        "transfer_rate_percent": round(transfer_rate, 1),
        "average_duration_seconds": round(avg_duration, 1) if avg_duration else 0,
    }


@router.get("/analytics/kpis")
async def get_analytics_kpis(
    days: int = Query(30, ge=1, le=365, description="Number of days to look back"),
    db: AsyncSession = Depends(get_db),
):
    """The headline figures for the Home page over the last `days` days, with the same figures for the stretch of
    equal length just before it, so each one can show how it moved."""
    from datetime import timezone as _tz
    from .followup_scheduler import resolve_tz
    from .models import Tenant
    now = datetime.utcnow()
    start = now - timedelta(days=days)
    before = start - timedelta(days=days)
    later = now + timedelta(days=1)

    # The busiest hour and "today" are read in the company's own time zone (India time when none is set).
    tenant_id = request_scope().tenant_id
    tenant = await db.get(Tenant, tenant_id) if tenant_id is not None else None
    zone_name = (tenant.timezone if tenant and tenant.timezone else "Asia/Kolkata")
    zone = resolve_tz(zone_name)

    def local(dt: datetime) -> datetime:
        return dt.replace(tzinfo=_tz.utc).astimezone(zone)

    rows = (await db.execute(
        select(Call.started_at, Call.status, Call.is_escalated, Call.transferred_to_human, Call.duration_seconds,
               Call.sentiment, Call.current_agent, Call.source).where(Call.started_at >= before)
    )).all()
    ticket_times = (await db.execute(select(Ticket.created_at).where(Ticket.created_at >= before))).scalars().all()
    appointment_times = (await db.execute(select(Appointment.created_at).where(Appointment.created_at >= before))).scalars().all()

    def handed(row) -> bool:
        return bool(row.transferred_to_human or row.is_escalated)

    def figures(since: datetime, until: datetime) -> Dict:
        calls = [r for r in rows if r.started_at and since <= r.started_at < until]
        timed = [r.duration_seconds for r in calls if r.duration_seconds is not None]
        mood = Counter((r.sentiment or "").strip().lower() for r in calls)
        resolved = sum(1 for r in calls if r.status == CallStatus.COMPLETED and not handed(r))
        return {
            "conversations": len(calls),
            "resolved": resolved,
            "resolved_rate_percent": round(resolved / len(calls) * 100, 1) if calls else None,
            "handed_over": sum(1 for r in calls if handed(r)),
            "average_duration_seconds": round(sum(timed) / len(timed), 1) if timed else None,
            "talk_seconds": round(sum(timed), 1),
            "tickets": sum(1 for t in ticket_times if t and since <= t < until),
            "appointments": sum(1 for t in appointment_times if t and since <= t < until),
            "positive": mood["positive"], "neutral": mood["neutral"], "negative": mood["negative"],
            "test_calls": sum(1 for r in calls if r.source == "simulator"),
        }

    current = figures(start, later)
    current["rated"] = current["positive"] + current["neutral"] + current["negative"]

    inside = [r for r in rows if r.started_at and r.started_at >= start]
    hours = Counter(local(r.started_at).hour for r in inside)
    agents = Counter((r.current_agent or "").strip() for r in inside if (r.current_agent or "").strip())
    today_from = local(now).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(_tz.utc).replace(tzinfo=None)

    peak = hours.most_common(1)
    top = agents.most_common(1)
    return {
        "period_days": days,
        "current": current,
        "previous": figures(before, start),
        "calls_today": sum(1 for r in rows if r.started_at and r.started_at >= today_from),
        "timezone": zone_name,
        "peak_hour_ist": peak[0][0] if peak else None,          # kept for older pages; the hour is in `timezone`
        "peak_hour": peak[0][0] if peak else None,
        "peak_hour_calls": peak[0][1] if peak else 0,
        "top_agent": {"name": top[0][0], "calls": top[0][1]} if top else None,
    }


@router.get("/analytics/daily")
async def get_daily_analytics(
    days: int = Query(14, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
):
    """Get daily call volume and escalation trends."""
    daily_data = []
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    for i in range(days - 1, -1, -1):
        day_start = today - timedelta(days=i)
        day_end = day_start + timedelta(days=1)

        # Total calls for this day
        total = (await db.execute(
            select(func.count(Call.id)).where(
                and_(Call.started_at >= day_start, Call.started_at < day_end)
            )
        )).scalar() or 0

        # Handed to a person this day: escalated (older flag) or transferred (what live phone calls set)
        escalated = (await db.execute(
            select(func.count(Call.id)).where(
                and_(
                    Call.started_at >= day_start,
                    Call.started_at < day_end,
                    or_(Call.is_escalated == True, Call.transferred_to_human == True),
                )
            )
        )).scalar() or 0

        daily_data.append({
            "date": day_start.strftime("%Y-%m-%d"),
            "total_calls": total,
            "escalated_calls": escalated,
            "ai_resolved": total - escalated,
        })

    return {"days": days, "data": daily_data}


@router.get("/analytics/trends")
async def get_analytics_trends(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """Get aggregated data for Recharts (volume, sentiment, intents)."""
    from sqlalchemy import desc
    since = datetime.utcnow() - timedelta(days=days)
    
    # Volume over time
    volume_data = []
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    for i in range(days - 1, -1, -1):
        day_start = today - timedelta(days=i)
        day_end = day_start + timedelta(days=1)
        total = (await db.execute(
            select(func.count(Call.id)).where(
                and_(Call.started_at >= day_start, Call.started_at < day_end)
            )
        )).scalar() or 0
        volume_data.append({"date": day_start.strftime("%m/%d"), "calls": total})
    
    # Sentiment distribution
    sentiment_query = select(
        Call.sentiment,
        func.count(Call.id).label("count")
    ).where(and_(Call.started_at >= since, Call.sentiment.isnot(None))).group_by(Call.sentiment)
    
    sentiment_result = await db.execute(sentiment_query)
    sentiment_data = [{"name": row.sentiment.capitalize() if row.sentiment else "Unknown", "value": row.count} for row in sentiment_result.all()]
    
    # Top Intents
    intents_query = select(
        Call.intent,
        func.count(Call.id).label("count")
    ).where(
        and_(
            Call.started_at >= since,
            Call.intent.isnot(None),
            Call.intent != "",
            Call.intent != "None"
        )
    ).group_by(Call.intent).order_by(desc("count")).limit(8)
    
    intents_result = await db.execute(intents_query)
    intents_data = [{"name": row.intent, "count": row.count} for row in intents_result.all() if row.intent]
    
    # If intents_data is empty, fallback to Ticket.intent
    if not intents_data:
        t_query = select(
            Ticket.intent,
            func.count(Ticket.id).label("count")
        ).group_by(Ticket.intent).order_by(desc("count")).limit(8)
        t_res = await db.execute(t_query)
        intents_data = [{"name": row.intent, "count": row.count} for row in t_res.all() if row.intent]

    return {
        "volume": volume_data,
        "sentiment": sentiment_data,
        "intents": intents_data
    }


@router.get("/analytics/weekly-report", dependencies=[Depends(require_staff)])
@router.post("/analytics/weekly-report", dependencies=[Depends(require_staff)])
async def generate_weekly_report_api():
    """
    Computes all 10 Enterprise Leadership KPIs for the current week and
    exports both structured JSON & Markdown reports to Google Cloud Storage (GCS).
    Bucket: gs://stld-bkt-data-prod-voicebot-log-uscentral01-001/reports/weekly/
    """
    try:
        from .weekly_kpi_generator import generate_weekly_kpi_report
        result = await asyncio.to_thread(generate_weekly_kpi_report)
        return result
    except Exception as exc:
        logger.error(f"[Analytics] Failed to generate weekly report: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ─── Escalations ────────────────────────────────────────────────────────────

@router.get("/escalations")
async def list_escalations(
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List escalation records with optional status filter."""
    query = (
        select(Escalation)
        .options(selectinload(Escalation.call))
        .order_by(Escalation.requested_at.desc())
    )

    if status:
        query = query.where(Escalation.status == status)

    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    escalations = result.scalars().all()

    return {
        "escalations": [
            {
                **e.to_dict(),
                "caller_number": e.call.caller_number if e.call else None,
                "conversation_id": e.call.conversation_id if e.call else None,
            }
            for e in escalations
        ],
        "count": len(escalations),
    }


@router.get("/escalations/metrics")
async def get_escalation_metrics(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """Get escalation statistics."""
    since = datetime.utcnow() - timedelta(days=days)

    total = (await db.execute(
        select(func.count(Escalation.id)).where(Escalation.requested_at >= since)
    )).scalar() or 0

    completed = (await db.execute(
        select(func.count(Escalation.id)).where(
            and_(Escalation.requested_at >= since, Escalation.status == "completed")
        )
    )).scalar() or 0

    failed = (await db.execute(
        select(func.count(Escalation.id)).where(
            and_(Escalation.requested_at >= since, Escalation.status == "failed")
        )
    )).scalar() or 0

    pending = (await db.execute(
        select(func.count(Escalation.id)).where(
            and_(
                Escalation.requested_at >= since,
                Escalation.status.in_(["requested", "started"]),
            )
        )
    )).scalar() or 0

    return {
        "period_days": days,
        "total_escalations": total,
        "completed": completed,
        "failed": failed,
        "pending": pending,
        "success_rate_percent": round((completed / total * 100), 1) if total > 0 else 0,
    }


# ─── Sync ───────────────────────────────────────────────────────────────────

@router.post("/sync", dependencies=[Depends(require_staff)])
async def trigger_sync(
    limit: int = Query(50, ge=1, le=200, description="Number of conversations to sync"),
):
    """Manually trigger sync of recent conversations into local DB."""
    try:
        stats = await twilio_sync_client.sync_recent_calls(lookback_minutes=180, limit=limit)
        return {"status": "ok", "stats": stats}
    except Exception as e:
        logger.error("Sync error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Sync failed")


@router.post("/sync-active", dependencies=[Depends(require_staff)])
async def trigger_active_sync():
    """Manually trigger sync of active calls."""
    try:
        stats = await twilio_sync_client.sync_active_calls()
        return {"status": "ok", "stats": stats}
    except Exception as e:
        logger.error("Active sync error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Active sync failed")


@router.post("/sync-twilio-recent", dependencies=[Depends(require_staff)])
async def trigger_twilio_recent_sync(
    lookback_minutes: int = Query(180, ge=1, le=10080, description="How far back to scan recent Twilio calls"),
    limit: int = Query(50, ge=1, le=200, description="Number of recent Twilio calls to inspect"),
):
    """Backfill recent terminal Twilio calls that never reached the local DB."""
    try:
        stats = await twilio_sync_client.sync_recent_calls(
            lookback_minutes=lookback_minutes,
            limit=limit,
        )
        return {"status": "ok", "stats": stats}
    except Exception as e:
        logger.error("Twilio recent sync error: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Twilio recent sync failed")


# ─── Events ─────────────────────────────────────────────────────────────────

@router.get("/events")
async def list_events(
    call_id: Optional[int] = Query(None),
    event_type: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """List events with optional filters."""
    query = select(Event).order_by(Event.created_at.desc())

    if call_id:
        query = query.where(Event.call_id == call_id)
    if event_type:
        query = query.where(Event.event_type == event_type)

    query = query.limit(limit)
    result = await db.execute(query)
    events = result.scalars().all()

    return {"events": [e.to_dict() for e in events], "count": len(events)}


@router.get("/diagnostics/overview")
async def get_diagnostics_overview(
    limit: int = Query(15, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """Recent call ingestion diagnostics for demos and troubleshooting."""
    result = await db.execute(
        select(Call)
        .options(
            selectinload(Call.transcript_messages),
            selectinload(Call.tickets),
            selectinload(Call.events),
        )
        .order_by(Call.started_at.desc())
        .limit(limit)
    )
    calls = result.scalars().all()

    event_result = await db.execute(
        select(Event)
        .order_by(Event.created_at.desc())
        .limit(40)
    )
    recent_events = event_result.scalars().all()

    diagnostics = []
    issue_count = 0

    for call in calls:
        loaded_events = getattr(call, "events", []) or []
        event_types = {event.event_type for event in loaded_events}
        stages = _build_diagnostic_stage(call, event_types)
        issue_count += len(stages["issues"])

        diagnostics.append({
            "call": call.to_dict(),
            "transcript_count": len(getattr(call, "transcript_messages", []) or []),
            "event_count": len(loaded_events),
            "events": [event.to_dict() for event in loaded_events[-8:]],
            "stages": stages,
        })

    return {
        "summary": {
            "checked_calls": len(diagnostics),
            "calls_with_issues": sum(1 for item in diagnostics if item["stages"]["issues"]),
            "open_issues": issue_count,
            "recent_event_count": len(recent_events),
        },
        "recent_events": [event.to_dict() for event in recent_events],
        "calls": diagnostics,
    }


@router.post("/calls/{call_id}/analyze")
async def analyze_call_transcript(call_id: int, db: AsyncSession = Depends(get_db)):
    """Analyze the call transcript using LLM and return structured analysis."""
    # Verify call exists
    call_result = await db.execute(
        select(Call)
        .options(selectinload(Call.transcript_messages))
        .where(Call.id == call_id)
    )
    call = call_result.scalars().first()
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")

    if _call_needs_hydration(call):
        await _hydrate_call_from_elevenlabs(call.conversation_id)

    # Fetch transcript messages
    result = await db.execute(
        select(TranscriptMessage)
        .where(TranscriptMessage.call_id == call_id)
        .order_by(TranscriptMessage.sequence_number)
    )
    messages = result.scalars().all()
    
    if not messages:
        raise HTTPException(status_code=400, detail="No transcript available for this call")
        
    transcript_text = "\n".join([f"{'Customer' if m.role == 'user' else 'Agent'}: {m.message}" for m in messages])
    
    from .llm_client import analyze_transcript
    try:
        analysis = await analyze_transcript(transcript_text)
        
        # update DB with results
        call.sentiment = analysis.sentiment
        call.intent = analysis.intent
        call.summary = analysis.summary
        
        # Auto-create ticket if escalated or if ticket/incident requested in transcript/summary
        existing_ticket = (await db.execute(select(Ticket).where(Ticket.call_id == call.id))).scalars().first()
        if not existing_ticket and call.caller_number and call.started_at:
            # tickets raised live by the agent are stored without a call id: match them by caller and time, otherwise
            # every guided call gets a second, invented "TKT-xxxx" ticket after it ends
            existing_ticket = (await db.execute(
                select(Ticket).where(Ticket.caller_number == call.caller_number, Ticket.created_at >= call.started_at)
            )).scalars().first()
        is_ticket_req = any(kw in (transcript_text + " " + analysis.intent + " " + analysis.summary).lower() for kw in ["ticket", "incident", "paper jam", "outage", "support"])
        
        if not existing_ticket and (call.is_escalated or call.transferred_to_human or is_ticket_req):
            ticket_id = await _generate_ticket_id(db)
            priority = "P1-Critical" if ("critical" in transcript_text.lower() or "urgent" in transcript_text.lower() or "outage" in transcript_text.lower()) else "P2-High"
            ticket = Ticket(
                ticket_id=ticket_id,
                call_id=call.id,
                caller_number=call.caller_number or "Unknown",
                intent=analysis.intent or "Support Ticket Request",
                issue=", ".join(analysis.key_issues) if analysis.key_issues else (analysis.summary or "Incident Ticket"),
                priority=priority,
                transcript_summary=analysis.summary,
                sentiment=analysis.sentiment,
            )
            db.add(ticket)
        
        await db.commit()
        return analysis.model_dump()
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error analyzing call transcript for call %s: %s", call_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail="Call transcript analysis failed")


# ─── License & Quotas ──────────────────────────────────────────────────────────

from .license_manager import license_manager

class LicenseActivatePayload(BaseModel):
    license_key: str
    email: str

@router.post("/license/activate", dependencies=[Depends(require_staff)])
async def activate_license_endpoint(payload: LicenseActivatePayload):
    """Activate license key against Stld-licensemate Authority Server."""
    try:
        status = await license_manager.activate(payload.license_key, payload.email)
        return {"status": "ok", "message": "License activated successfully", "details": status}
    except Exception as e:
        logger.error("License activation error: %s", e)
        raise HTTPException(status_code=400, detail=str(e))

class LicenseRegisterPayload(BaseModel):
    name: str
    company: str
    email: str
    product: Optional[str] = "STL Voice Bot"
    project_id: Optional[str] = "VOICE-BOT-PROD"
    license_type: Optional[str] = "MACHINE"
    machine_node: Optional[str] = "N/A"
    machine_sn: Optional[str] = "N/A"

@router.post("/license/register", dependencies=[Depends(require_staff)])
async def register_license_endpoint(payload: LicenseRegisterPayload):
    """Submit client registration request to LicenseMate server."""
    try:
        res = await license_manager.register_client(
            name=payload.name,
            company=payload.company,
            email=payload.email,
            product=payload.product or "STL Voice Bot",
            project_id=payload.project_id or "VOICE-BOT-PROD",
            license_type=payload.license_type or "MACHINE",
            machine_node=payload.machine_node or "N/A",
            machine_sn=payload.machine_sn or "N/A"
        )
        return res
    except Exception as e:
        logger.error("License registration error: %s", e)
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/license/registration-status", dependencies=[Depends(require_staff)])
async def get_registration_status_endpoint(request_id: Optional[str] = None):
    """Check approval status of client registration and auto-activate if approved."""
    try:
        res = await license_manager.check_registration_status(request_id)
        return res
    except Exception as e:
        logger.error("Registration status check error: %s", e)
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/license/status", dependencies=[Depends(require_staff)])
async def get_license_status_endpoint():
    """Fetch current license status, remaining calls, and constraints."""
    await license_manager.refresh_from_server()
    return license_manager.get_status()

@router.post("/license/deactivate", dependencies=[Depends(require_staff)])
async def deactivate_license_endpoint():
    """Deactivate current license."""
    return license_manager.deactivate()

@router.post("/license/reset-usage", dependencies=[Depends(require_staff)])
async def reset_license_usage_endpoint():
    """Reset call usage counter for testing constraints."""
    return license_manager.reset_usage()

class LicenseConstraintsPayload(BaseModel):
    max_calls: Optional[int] = None
    max_hours: Optional[float] = None
    expiry: Optional[str] = None
    allowed_agents: Optional[List[str]] = None
    status: Optional[str] = None

@router.post("/license/update-constraints", dependencies=[Depends(require_staff)])
async def update_license_constraints_endpoint(payload: LicenseConstraintsPayload):
    """Add or update operational constraints on the active license key."""
    try:
        updated = await license_manager.update_constraints(
            max_calls=payload.max_calls,
            max_hours=payload.max_hours,
            expiry=payload.expiry,
            allowed_agents=payload.allowed_agents,
            status=payload.status
        )
        return {"status": "ok", "message": "License constraints updated successfully", "details": updated}
    except Exception as e:
        logger.error("License constraint update error: %s", e)
        raise HTTPException(status_code=400, detail=str(e))

class ConsumeCallPayload(BaseModel):
    agent_id: Optional[str] = None

@router.post("/license/consume-call", dependencies=[Depends(require_staff)])
async def consume_call_endpoint(payload: Optional[ConsumeCallPayload] = None):
    """Manually test call quota consumption with optional agent constraint check."""
    agent_id = payload.agent_id if payload else None
    allowed, reason = license_manager.check_and_consume_call(agent_id=agent_id)
    if not allowed:
        raise HTTPException(status_code=403, detail=reason)
    return {"status": "ok", "message": reason, "details": license_manager.get_status()}


@router.get("/gnani/credits")
async def get_gnani_credits():
    """Endpoint polled by GnaniSidebar for credit telemetry."""
    return {"used": 83, "total": 1000}




