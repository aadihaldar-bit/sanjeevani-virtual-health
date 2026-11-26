"""
Public Outbound Voice API router for external system integrations.
Prefix: /api/v1/outbound
Authentication: API Key (Bearer stl_outbound_...)
"""
import json
import logging
import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db
from .models import Call, APIClient, CallStatus
from .outbound_api_auth import verify_api_key
from .outbound_api_models import (
    OutboundCallAPIRequest,
    OutboundCallAPIResponse,
    CallStatusResponse
)
from .api_routes import _place_outbound_call

logger = logging.getLogger(__name__)

outbound_router = APIRouter(prefix="/api/v1/outbound", tags=["outbound-voice-api"])

# Fallback in-memory storage for outbound call context if Redis is offline
IN_MEMORY_OUTBOUND_CONTEXT: dict = {}

async def store_outbound_context(key: str, data: dict):
    """Stores outbound call context in Redis or in-memory dict."""
    try:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        import redis.asyncio as aioredis
        r = aioredis.from_url(redis_url, socket_timeout=2.0)
        await r.setex(f"outbound_ctx:{key}", 1800, json.dumps(data))
        await r.aclose()
    except Exception as e:
        logger.warning(f"[OutboundAPI] Redis store context notice ({e}); using in-memory store")
        IN_MEMORY_OUTBOUND_CONTEXT[f"outbound_ctx:{key}"] = data

async def retrieve_outbound_context(key: str) -> Optional[dict]:
    """Retrieves and pops outbound call context from Redis or in-memory dict."""
    try:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        import redis.asyncio as aioredis
        r = aioredis.from_url(redis_url, socket_timeout=2.0)
        raw = await r.get(f"outbound_ctx:{key}")
        if raw:
            await r.delete(f"outbound_ctx:{key}")
            await r.aclose()
            return json.loads(raw)
        await r.aclose()
    except Exception as e:
        logger.warning(f"[OutboundAPI] Redis retrieve context notice ({e}); checking in-memory store")
    
    return IN_MEMORY_OUTBOUND_CONTEXT.pop(f"outbound_ctx:{key}", None)


@outbound_router.post("/call", response_model=OutboundCallAPIResponse, status_code=202)
async def initiate_api_outbound_call(
    payload: OutboundCallAPIRequest,
    client: APIClient = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Initiates an automated AI outbound voice call to the specified phone number.
    Supports dynamic context injection, custom greeting, and personalized follow-up Q&A.
    """
    logger.info(f"[OutboundAPI] Request from client '{client.client_name}' (ID: {client.id}) to {payload.phone_number}")

    # Build greeting
    greeting = payload.greeting_template
    if not greeting:
        name_str = f" {payload.recipient_name}" if payload.recipient_name else ""
        greeting = f"Hello{name_str}, I am calling regarding: {payload.message_context}"

    # Build system prompt overlay
    system_prompt = payload.system_prompt
    if not system_prompt:
        recipient_clause = f" to {payload.recipient_name}" if payload.recipient_name else ""
        system_prompt = (
            f"You are an AI assistant placing an automated voice call{recipient_clause}.\n"
            f"Reason for call / Context: {payload.message_context}\n"
            f"Instructions:\n"
            f"- Explain the situation clearly and concisely.\n"
            f"- Be professional, respectful, and helpful.\n"
            f"- Answer any questions or concerns the recipient asks back.\n"
        )

    outbound_ctx = {
        "client_id": client.id,
        "client_name": client.client_name,
        "tenant_id": getattr(client, "tenant_id", None),
        "recipient_name": payload.recipient_name,
        "message_context": payload.message_context,
        "greeting": greeting,
        "system_prompt": system_prompt,
        "voice": payload.voice or "Aarohi",
        "callback_url": payload.callback_url or client.callback_url,
        "metadata": payload.metadata or {},
        "created_at": datetime.utcnow().isoformat()
    }

    # An API client that belongs to an organization calls out from that organization's own number.
    # Clients with no organization keep the platform number.
    own_caller_id = None
    if getattr(client, "tenant_id", None) is not None:
        from .numbers import pick_caller_id
        own_caller_id = await pick_caller_id(db, client.tenant_id)
        if own_caller_id is None:
            raise HTTPException(status_code=409, detail="Your organization has no outbound phone number allocated. Ask your administrator.")

    # Trigger outbound telephony call (Smartflo/Twilio)
    call_result = await _place_outbound_call(payload.phone_number, payload.provider, caller_id=own_caller_id)
    clean_e164 = call_result.get("clean_e164", payload.phone_number)
    call_sid = call_result.get("call_sid") or call_result.get("ref_id") or f"outbound-{int(datetime.utcnow().timestamp())}"

    # Store context for phone number AND call_sid so media_bridge can retrieve it when call connects
    await store_outbound_context(clean_e164, outbound_ctx)
    if call_sid:
        await store_outbound_context(call_sid, outbound_ctx)

    # Increment client counters
    client.calls_today += 1
    client.calls_this_month += 1
    await db.commit()

    # Create initial Call db record
    db_call = Call(
        call_sid=call_sid,
        caller_number=own_caller_id or ("+918069879488" if call_result.get("provider") == "smartflo" else os.getenv("TWILIO_PHONE_NUMBER", "")),
        tenant_id=getattr(client, "tenant_id", None),
        called_number=clean_e164,
        status=CallStatus.ACTIVE,
        live_status="queued",
        source="api-outbound",
        api_client_id=client.id,
        metadata_json=payload.metadata,
        current_agent="Outbound API Agent",
        started_at=datetime.utcnow()
    )
    db.add(db_call)
    await db.commit()

    name_display = payload.recipient_name or clean_e164
    return OutboundCallAPIResponse(
        status="ok",
        call_id=str(db_call.id),
        message=f"Outbound call to {name_display} successfully queued via {call_result.get('provider')}",
        provider=call_result.get("provider"),
        ref_id=call_result.get("ref_id") or call_result.get("call_sid")
    )


@outbound_router.get("/calls/{call_id}/status", response_model=CallStatusResponse)
async def get_api_call_status(
    call_id: str,
    client: APIClient = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db)
):
    """Poll the status, transcript, and summary of an outbound API call."""
    stmt = select(Call).where(Call.api_client_id == client.id)
    if call_id.isdigit():
        stmt = stmt.where((Call.id == int(call_id)) | (Call.call_sid == call_id))
    else:
        stmt = stmt.where(Call.call_sid == call_id)

    result = await db.execute(stmt)
    call = result.scalars().first()

    if not call:
        raise HTTPException(status_code=404, detail=f"Call '{call_id}' not found for this API client")

    detail = call.to_detail_dict()
    return CallStatusResponse(
        call_id=str(call.id),
        status=call.status,
        live_status=call.live_status,
        duration_seconds=call.duration_seconds,
        started_at=call.started_at.isoformat() if call.started_at else None,
        ended_at=call.ended_at.isoformat() if call.ended_at else None,
        transcript=detail.get("transcript", []),
        summary=call.summary,
        sentiment=call.sentiment,
        metadata=call.metadata_json
    )


@outbound_router.get("/usage")
async def get_api_usage(client: APIClient = Depends(verify_api_key)):
    """Returns quota usage details for the authenticated API client."""
    return {
        "client_name": client.client_name,
        "client_company": client.client_company,
        "calls_today": client.calls_today,
        "daily_call_quota": client.daily_call_quota,
        "daily_remaining": max(0, client.daily_call_quota - client.calls_today),
        "calls_this_month": client.calls_this_month,
        "monthly_call_quota": client.monthly_call_quota,
        "monthly_remaining": max(0, client.monthly_call_quota - client.calls_this_month),
        "rate_limit_per_minute": client.rate_limit_per_minute
    }
