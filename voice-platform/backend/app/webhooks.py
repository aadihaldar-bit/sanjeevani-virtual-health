"""
Webhook Endpoints
Receives post-call data from ElevenLabs and status callbacks from Twilio.
"""

import os
import hmac
import hashlib
import logging
from datetime import datetime
import httpx
import asyncio

from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db
from .models import Call, TranscriptMessage, Event, Escalation, CallStatus
from .websocket_manager import dashboard_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["webhooks"])

TWILIO_ACTIVE_STATUSES = {"initiated", "ringing", "answered", "in-progress"}
TWILIO_TERMINAL_STATUSES = {"completed", "busy", "no-answer", "failed", "canceled"}
TWILIO_FAILED_STATUSES = {"busy", "no-answer", "failed", "canceled"}


def _normalize_twilio_live_status(call_status: str) -> str:
    status = (call_status or "").lower()
    if status == "answered":
        return "in-progress"
    return status or "initiated"


def _map_terminal_call_status(call_status: str) -> str:
    return CallStatus.FAILED if call_status in TWILIO_FAILED_STATUSES else CallStatus.COMPLETED


def verify_elevenlabs_signature(payload: bytes, signature_header: str, secret: str) -> bool:
    """
    Verify HMAC signature from ElevenLabs webhook.
    Header format: t=<timestamp>,v0=<hash>
    HMAC is computed over: <timestamp>.<body>
    """
    if not secret:
        return True  # Skip verification if no secret configured

    try:
        # Parse header: t=1234567890,v0=abcdef...
        parts = {}
        for item in signature_header.split(","):
            key, value = item.split("=", 1)
            parts[key.strip()] = value.strip()

        timestamp = parts.get("t", "")
        received_hash = parts.get("v0", "")

        if not timestamp or not received_hash:
            logger.warning("Invalid signature header format")
            return False

        # Compute HMAC over timestamp.body
        message = f"{timestamp}.".encode("utf-8") + payload
        expected = hmac.new(
            secret.encode("utf-8"),
            message,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(expected, received_hash)
    except Exception as e:
        logger.error(f"Signature verification error: {e}")
        return False


# ─── TwiML Voice Webhook for Incoming Calls ───────────────────────────────────

@router.post("/twilio/voice")
async def twilio_incoming_call(request: Request, db: AsyncSession = Depends(get_db)):
    """
    TwiML webhook for incoming calls to the Twilio phone number.
    This endpoint must be configured in Twilio console under:
    Phone Numbers → Your Number → Voice & Fax → A Call Comes In → Webhook URL
    
    Redirects incoming calls to the ElevenLabs AI agent webhook.
    """
    from twilio.twiml.voice_response import VoiceResponse
    
    try:
        form_data = await request.form()
        call_sid = form_data.get("CallSid", "")
        from_number = form_data.get("From", "")
        to_number = form_data.get("To", "")
        call_status = form_data.get("CallStatus", "initiated")
        
        logger.info(
            f"[INCOMING CALL] SID={call_sid} | From={from_number} | To={to_number} | Status={call_status}"
        )
        
        # Store call in database immediately
        timestamp = datetime.utcnow()
        result = await db.execute(select(Call).where(Call.call_sid == call_sid))
        call = result.scalar_one_or_none()
        
        if not call:
            call = Call(
                call_sid=call_sid,
                caller_number=from_number or "Unknown",
                called_number=to_number or "",
                agent_id=os.getenv("ELEVENLABS_AGENT_ID", ""),
                status=CallStatus.ACTIVE,
                live_status="initiated",
                source="twilio",
                started_at=timestamp,
                last_event_at=timestamp,
                current_agent=os.getenv("ELEVENLABS_AGENT_ID", ""),
            )
            db.add(call)
            await db.flush()
            logger.info(f"[DB] Created call record: id={call.id}")
        
        db.add(Event(
            call_id=call.id,
            event_type="twilio_incoming_voice_webhook",
            payload={
                "call_sid": call_sid,
                "from": from_number,
                "to": to_number,
                "call_status": call_status,
            },
        ))
        await db.commit()
        
        # Get ElevenLabs agent ID from environment
        agent_id = os.getenv("ELEVENLABS_AGENT_ID", "").strip()
        if not agent_id:
            logger.error("[ERROR] ELEVENLABS_AGENT_ID not configured")
            response = VoiceResponse()
            response.say("The voice agent is not properly configured. Please contact support.")
            return str(response)
        
        # Create TwiML response that redirects to ElevenLabs Twilio webhook
        elevenlabs_webhook_url = (
            f"https://api.elevenlabs.io/v1/convai/phone/twilio/webhook/{agent_id}"
        )
        
        logger.info(
            f"[REDIRECT] Routing to ElevenLabs: {elevenlabs_webhook_url}"
        )
        
        response = VoiceResponse()
        response.redirect(elevenlabs_webhook_url, method="POST")
        
        logger.info(f"[TWIML] Returning redirect response to Twilio")
        return str(response)
        
    except Exception as e:
        logger.error(f"[ERROR] Incoming call TwiML webhook error: {e}", exc_info=True)
        try:
            response = VoiceResponse()
            response.say("An application error occurred while connecting your call. Our support team has been notified.")
            return str(response)
        except:
            return "<Response></Response>"


@router.post("/elevenlabs")
async def elevenlabs_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Receives post-call data from ElevenLabs.

    ElevenLabs sends this webhook after a conversation ends with:
    - conversation_id
    - transcript (array of {role, message})
    - analysis (summary, sentiment, evaluation)
    - metadata (call details)
    """
    try:
        # Read raw body for signature verification
        body = await request.body()

        # Verify signature if secret is configured
        webhook_secret = os.getenv("ELEVENLABS_WEBHOOK_SECRET", "")
        signature = request.headers.get("ElevenLabs-Signature", "")
        if webhook_secret and not verify_elevenlabs_signature(body, signature, webhook_secret):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

        data = await request.json()
        event_type = data.get("type", "post_call")
        logger.info(f"ElevenLabs webhook received: {event_type}")

        # Log raw payload keys for debugging
        logger.info(f"Webhook top-level keys: {list(data.keys())}")

        # ElevenLabs may nest the actual data under a 'data' key
        # Format: { "type": "post_call_transcription", "event_id": "...", "data": { ... } }
        payload = data.get("data", data)
        if isinstance(payload, dict):
            logger.info(f"Payload keys: {list(payload.keys())}")

        # Extract fields — try nested 'data' first, then top-level
        conversation_id = (
            payload.get("conversation_id")
            or data.get("conversation_id")
            or ""
        )
        agent_id = (
            payload.get("agent_id")
            or data.get("agent_id")
            or ""
        )

        # Transcript may be under different keys
        transcript_data = (
            payload.get("transcript", [])
            or data.get("transcript", [])
        )
        # ElevenLabs might send transcript as an object with 'messages' key
        if isinstance(transcript_data, dict):
            transcript_data = transcript_data.get("messages", [])

        analysis = payload.get("analysis", data.get("analysis", {}))
        metadata = payload.get("metadata", data.get("metadata", {}))

        # Timestamps — check multiple possible locations
        start_time_unix = (
            payload.get("start_time_unix")
            or data.get("start_time_unix")
            or payload.get("start_time")
            or payload.get("call_start_time")
        )
        end_time_unix = (
            payload.get("end_time_unix")
            or data.get("end_time_unix")
            or payload.get("end_time")
            or payload.get("call_end_time")
        )

        # Handle string timestamps
        if isinstance(start_time_unix, str):
            try:
                start_time_unix = float(start_time_unix)
            except ValueError:
                start_time_unix = None
        if isinstance(end_time_unix, str):
            try:
                end_time_unix = float(end_time_unix)
            except ValueError:
                end_time_unix = None

        started_at = datetime.utcfromtimestamp(start_time_unix) if start_time_unix else datetime.utcnow()
        ended_at = datetime.utcfromtimestamp(end_time_unix) if end_time_unix else datetime.utcnow()

        # Duration — also check for a direct duration field
        duration = payload.get("call_duration_secs") or payload.get("duration")
        if duration is None and start_time_unix and end_time_unix:
            duration = end_time_unix - start_time_unix

        logger.info(
            f"Parsed: conversation_id={conversation_id}, agent_id={agent_id}, "
            f"transcript_msgs={len(transcript_data)}, duration={duration}"
        )

        # Detect escalation
        is_escalated = _detect_escalation_from_data(transcript_data, analysis, data)
        summary = analysis.get("summary", data.get("summary", ""))
        sentiment = analysis.get("sentiment", "")

        phone_call = metadata.get("phone_call") or {}
        call_sid = (
            data.get("call_sid")
            or payload.get("call_sid")
            or phone_call.get("call_sid")
        )

        # Check if call already exists
        result = await db.execute(select(Call).where(Call.conversation_id == conversation_id))
        call = result.scalar_one_or_none()
        if not call and call_sid:
            result = await db.execute(select(Call).where(Call.call_sid == call_sid))
            call = result.scalar_one_or_none()

        if call:
            # Update existing call
            call.conversation_id = conversation_id or call.conversation_id
            call.call_sid = call_sid or call.call_sid
            call.status = CallStatus.COMPLETED
            call.live_status = "completed"
            call.source = "elevenlabs"
            call.ended_at = ended_at
            call.last_event_at = ended_at
            call.duration_seconds = duration
            call.end_reason = data.get("end_reason", "completed")
            call.is_escalated = is_escalated
            call.transferred_to_human = is_escalated
            call.summary = summary
            call.sentiment = sentiment
            call.metadata_json = metadata
        else:
            # Create new call record
            call = Call(
                conversation_id=conversation_id,
                call_sid=call_sid,
                caller_number=(
                    metadata.get("caller_number")
                    or payload.get("caller_number")
                    or metadata.get("from")
                    or payload.get("phone_number")
                    or data.get("caller_number")
                    or "Unknown"
                ),
                called_number=(
                    metadata.get("called_number")
                    or payload.get("called_number")
                    or metadata.get("to")
                    or data.get("called_number")
                    or ""
                ),
                agent_id=agent_id,
                status=CallStatus.COMPLETED,
                live_status="completed",
                source="elevenlabs",
                started_at=started_at,
                ended_at=ended_at,
                last_event_at=ended_at,
                duration_seconds=duration,
                end_reason=data.get("end_reason", "completed"),
                is_escalated=is_escalated,
                transferred_to_human=is_escalated,
                current_agent=agent_id,
                sentiment=sentiment,
                summary=summary,
                metadata_json=metadata,
            )
            db.add(call)

        await db.flush()

        # Store transcript messages
        for idx, msg in enumerate(transcript_data):
            # Check if this message index already exists to avoid duplication with live stream logging
            existing_msg_result = await db.execute(
                select(TranscriptMessage).where(
                    TranscriptMessage.call_id == call.id,
                    TranscriptMessage.sequence_number == idx
                )
            )
            if not existing_msg_result.scalar_one_or_none():
                transcript_msg = TranscriptMessage(
                    call_id=call.id,
                    role=msg.get("role", "unknown"),
                    message=msg.get("message") or msg.get("text") or msg.get("content") or "",
                    timestamp=started_at,
                    sequence_number=idx,
                )
                db.add(transcript_msg)

        # Store event
        event = Event(
            call_id=call.id,
            event_type="call_completed",
            payload={
                "source": "elevenlabs_webhook",
                "conversation_id": conversation_id,
                "end_reason": data.get("end_reason", ""),
            },
        )
        db.add(event)

        # Store escalation if detected
        if is_escalated:
            escalation = Escalation(
                call_id=call.id,
                reason=analysis.get("escalation_reason", "Detected from conversation"),
                status="completed",
                requested_at=started_at,
                completed_at=ended_at,
            )
            db.add(escalation)

        await db.commit()

        # Broadcast to dashboard via WebSocket
        await dashboard_manager.broadcast({
            "type": "call_completed",
            "call": call.to_dict(),
            "has_transcript": len(transcript_data) > 0,
            "is_escalated": is_escalated,
        })

        logger.info(
            f"Webhook processed: conversation={conversation_id}, "
            f"messages={len(transcript_data)}, escalated={is_escalated}"
        )

        return {"status": "ok", "conversation_id": conversation_id}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing ElevenLabs webhook: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Webhook processing error: {str(e)}")


async def _relay_to_elevenlabs(form_data: dict):
    """Transparently forward Twilio status payloads to ElevenLabs original endpoint."""
    try:
        url = "https://api.us.elevenlabs.io/twilio/status-callback"
        async with httpx.AsyncClient(timeout=5.0) as client:
            # Filter out complex internal structures, pass standard flattened form values
            payload = {k: str(v) for k, v in form_data.items()}
            resp = await client.post(url, data=payload)
            logger.info(f"ElevenLabs Relay response: {resp.status_code}")
    except Exception as e:
        logger.warning(f"Background relay to ElevenLabs failed: {e}")

@router.post("/twilio/status")
async def twilio_status_callback(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Receives Twilio call status callbacks.
    Tracks call start/end from Twilio's perspective.
    """
    try:
        form_data = await request.form()
        
        # Transparently relay in the background so we don't delay Twilio
        asyncio.create_task(_relay_to_elevenlabs(dict(form_data)))

        call_sid = form_data.get("CallSid", "")
        call_status = form_data.get("CallStatus", "")
        caller = form_data.get("From", "")
        called = form_data.get("To", "")
        call_status = (call_status or "").lower()
        timestamp = datetime.utcnow()

        logger.info(
            "Twilio status callback received: sid=%s status=%s from=%s to=%s payload_keys=%s",
            call_sid,
            call_status,
            caller,
            called,
            sorted(form_data.keys()),
        )

        result = await db.execute(select(Call).where(Call.call_sid == call_sid))
        call = result.scalar_one_or_none()

        if call_status in TWILIO_ACTIVE_STATUSES:
            live_status = _normalize_twilio_live_status(call_status)

            if not call:
                call = Call(
                    call_sid=call_sid,
                    caller_number=caller,
                    called_number=called,
                    status=CallStatus.ACTIVE,
                    live_status=live_status,
                    source="twilio",
                    started_at=timestamp,
                    last_event_at=timestamp,
                    current_agent=os.getenv("ELEVENLABS_AGENT_ID", ""),
                )
                db.add(call)
                await db.flush()
                logger.info("Created live call row from Twilio event: sid=%s live_status=%s", call_sid, live_status)
            else:
                call.status = CallStatus.ACTIVE
                call.live_status = live_status
                call.source = "twilio"
                call.last_event_at = timestamp
                call.ended_at = None
                call.end_reason = None
                call.caller_number = caller or call.caller_number
                call.called_number = called or call.called_number
                if not call.started_at:
                    call.started_at = timestamp
                logger.info("Updated live call row from Twilio event: sid=%s live_status=%s", call_sid, live_status)

            db.add(Event(
                call_id=call.id,
                event_type=f"twilio_{live_status}",
                payload={
                    "call_sid": call_sid,
                    "call_status": call_status,
                    "live_status": live_status,
                    "from": caller,
                    "to": called,
                },
            ))
            await db.commit()

            await dashboard_manager.broadcast({
                "type": "call_updated",
                "call": call.to_dict(),
                "source": "twilio_status_callback",
            })
            return {"status": "ok"}

        if call_status in TWILIO_TERMINAL_STATUSES:
            if call:
                call.status = _map_terminal_call_status(call_status)
                call.live_status = call_status
                call.source = "twilio"
                call.ended_at = timestamp
                call.last_event_at = timestamp
                if call.started_at:
                    call.duration_seconds = (call.ended_at - call.started_at).total_seconds()
                call.end_reason = call_status

                db.add(Event(
                    call_id=call.id,
                    event_type="call_ended",
                    payload={"call_sid": call_sid, "status": call_status},
                ))
                await db.commit()
                logger.info("Marked call terminal from Twilio event: sid=%s status=%s", call_sid, call_status)

                await dashboard_manager.broadcast({
                    "type": "call_completed" if call.status == CallStatus.COMPLETED else "call_failed",
                    "call": call.to_dict(),
                    "source": "twilio_status_callback",
                })
            else:
                logger.warning("Received terminal Twilio event for unknown call sid=%s status=%s", call_sid, call_status)
            return {"status": "ok"}

        logger.info(f"Twilio status callback: SID={call_sid}, Status={call_status}")

        if call_status in ("ringing", "answered"):
            # Call started/answered — create or update record
            result = await db.execute(
                select(Call).where(Call.call_sid == call_sid)
            )
            call = result.scalar_one_or_none()

            if not call:
                call = Call(
                    call_sid=call_sid,
                    caller_number=caller,
                    called_number=called,
                    status=CallStatus.ACTIVE,
                    started_at=datetime.utcnow(),
                    current_agent=os.getenv("ELEVENLABS_AGENT_ID", ""),
                )
                db.add(call)
                await db.flush()

                event = Event(
                    call_id=call.id,
                    event_type="call_started",
                    payload={"call_sid": call_sid, "from": caller, "to": called},
                )
                db.add(event)
                await db.commit()

                # Broadcast to dashboard
                await dashboard_manager.broadcast({
                    "type": "call_started",
                    "call": call.to_dict(),
                })
            elif call_status == "answered" and call.status == CallStatus.ACTIVE:
                # Update call to reflect it was answered
                event = Event(
                    call_id=call.id,
                    event_type="call_answered",
                    payload={"call_sid": call_sid},
                )
                db.add(event)
                await db.commit()

        elif call_status in ("completed", "busy", "no-answer", "failed", "canceled"):
            # Call ended
            result = await db.execute(
                select(Call).where(Call.call_sid == call_sid)
            )
            call = result.scalar_one_or_none()

            if call and call.status == CallStatus.ACTIVE:
                call.status = CallStatus.COMPLETED
                call.ended_at = datetime.utcnow()
                if call.started_at:
                    call.duration_seconds = (call.ended_at - call.started_at).total_seconds()
                call.end_reason = call_status

                event = Event(
                    call_id=call.id,
                    event_type="call_ended",
                    payload={"call_sid": call_sid, "status": call_status},
                )
                db.add(event)
                await db.commit()

                await dashboard_manager.broadcast({
                    "type": "call_completed",
                    "call": call.to_dict(),
                })

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Error processing Twilio status callback: {e}", exc_info=True)
        return {"status": "error", "detail": str(e)}


@router.post("/event")
async def generic_event(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Generic event ingestion endpoint.
    Accepts custom events: escalation_triggered, human_handoff_started,
    human_handoff_completed, unknown_query_detected, agent_switched, etc.
    """
    try:
        data = await request.json()
        event_type = data.get("type", "unknown")
        conversation_id = data.get("conversation_id", "")
        call_sid = data.get("call_sid", "")

        logger.info(f"Event received: type={event_type}")

        # Find associated call
        call = None
        if conversation_id:
            result = await db.execute(
                select(Call).where(Call.conversation_id == conversation_id)
            )
            call = result.scalar_one_or_none()
        elif call_sid:
            result = await db.execute(
                select(Call).where(Call.call_sid == call_sid)
            )
            call = result.scalar_one_or_none()

        # Store event
        event = Event(
            call_id=call.id if call else None,
            event_type=event_type,
            payload=data,
        )
        db.add(event)

        # Handle specific event types
        if event_type == "escalation_triggered" and call:
            call.is_escalated = True
            escalation = Escalation(
                call_id=call.id,
                reason=data.get("reason", ""),
                status="requested",
                requested_at=datetime.utcnow(),
            )
            db.add(escalation)

        elif event_type == "human_handoff_started" and call:
            call.transferred_to_human = True
            # Update latest escalation
            result = await db.execute(
                select(Escalation)
                .where(Escalation.call_id == call.id)
                .order_by(Escalation.requested_at.desc())
            )
            esc = result.scalar_one_or_none()
            if esc:
                esc.status = "started"
                esc.started_at = datetime.utcnow()
                esc.transferred_to = data.get("transferred_to", "")

        elif event_type == "human_handoff_completed" and call:
            result = await db.execute(
                select(Escalation)
                .where(Escalation.call_id == call.id)
                .order_by(Escalation.requested_at.desc())
            )
            esc = result.scalar_one_or_none()
            if esc:
                esc.status = "completed"
                esc.completed_at = datetime.utcnow()

        elif event_type == "agent_switched" and call:
            call.current_agent = data.get("new_agent", call.current_agent)

        await db.commit()

        # Broadcast to dashboard
        await dashboard_manager.broadcast({
            "type": event_type,
            "call": call.to_dict() if call else None,
            "event": data,
        })

        return {"status": "ok", "event_type": event_type}

    except Exception as e:
        logger.error(f"Error processing event: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


def _detect_escalation_from_data(transcript: list, analysis: dict, data: dict) -> bool:
    """Detect escalation from webhook payload data."""
    if analysis.get("escalated") or analysis.get("transferred"):
        return True
    if data.get("is_escalated") or data.get("transferred_to_human"):
        return True

    evaluation = analysis.get("evaluation", {})
    if evaluation.get("escalation_detected"):
        return True

    escalation_keywords = [
        "transfer", "human agent", "speak to a person",
        "supervisor", "escalat", "handoff", "hand off",
        "connect me to", "real person", "live agent",
    ]
    for msg in transcript:
        text = (msg.get("message") or msg.get("text") or msg.get("content") or "").lower()
        if any(kw in text for kw in escalation_keywords):
            return True

    return False
