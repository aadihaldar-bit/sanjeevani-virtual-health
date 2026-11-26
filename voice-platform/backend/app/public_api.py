"""
STL Voice Agent — Public API v1
Prefix: /api/v1/
Authentication: Bearer stl_outbound_<token>   (except /api/v1/auth/register which is open)

Endpoint groups:
  Auth          POST   /api/v1/auth/register
  Me            GET    /api/v1/me
                POST   /api/v1/me/rotate-key
  Chat          POST   /api/v1/chat
                POST   /api/v1/chat/sessions
                GET    /api/v1/chat/sessions/{session_id}
  TTS           POST   /api/v1/tts/synthesize
                GET    /api/v1/tts/voices
  Outbound      GET    /api/v1/outbound/calls          (list)
                DELETE /api/v1/outbound/calls/{id}     (cancel)
  Tickets       POST   /api/v1/tickets
                GET    /api/v1/tickets
                GET    /api/v1/tickets/{ticket_id}
                PATCH  /api/v1/tickets/{ticket_id}
  Analytics     GET    /api/v1/analytics/calls
  Webhooks      POST   /api/v1/webhooks
                GET    /api/v1/webhooks
                DELETE /api/v1/webhooks/{webhook_id}
"""
import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional, List

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db
from .models import (
    APIClient, Call, CallStatus, Ticket, ChatSession,
    WebhookEndpoint
)
from .outbound_api_auth import verify_api_key, generate_api_key, hash_api_key
from .public_api_models import (
    RegisterAPIClientRequest, RegisterAPIClientResponse,
    MeResponse, RotateKeyResponse,
    ChatRequest, ChatResponse, CreateSessionRequest, SessionResponse,
    TTSSynthesizeRequest, TTSSynthesizeResponse, VoiceInfo,
    ListCallsResponse,
    CreateTicketRequest, UpdateTicketRequest, TicketResponse,
    AnalyticsResponse,
    CreateWebhookRequest, WebhookResponse,
    JevDecisionRequest, JevDecisionResponse,
)
from .jev_client import JevDecisionEngine


logger = logging.getLogger(__name__)

public_api_router = APIRouter(prefix="/api/v1", tags=["public-api-v1"])

# Free-tier quotas for self-registered users
FREE_DAILY_QUOTA = 10
FREE_MONTHLY_QUOTA = 100
FREE_RATE_LIMIT = 5


# ─── Helpers ──────────────────────────────────────────────────────────────────

async def _fire_webhooks(
    db: AsyncSession,
    client: APIClient,
    event: str,
    payload: dict,
):
    """Fire all registered webhook endpoints for a client that subscribe to this event."""
    stmt = select(WebhookEndpoint).where(
        WebhookEndpoint.api_client_id == client.id,
        WebhookEndpoint.is_active == True,
    )
    result = await db.execute(stmt)
    endpoints = result.scalars().all()
    body = json.dumps({"event": event, "timestamp": datetime.utcnow().isoformat(), "data": payload})
    for ep in endpoints:
        if event not in (ep.events or []):
            continue
        headers = {"Content-Type": "application/json"}
        if ep.secret:
            sig = hmac.new(ep.secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
            headers["X-STL-Signature"] = f"sha256={sig}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as hc:
                await hc.post(ep.url, content=body, headers=headers)
        except Exception as exc:
            logger.warning(f"[PublicAPI] Webhook delivery failed to {ep.url}: {exc}")


# ─── Auth ─────────────────────────────────────────────────────────────────────

@public_api_router.post("/auth/register", response_model=RegisterAPIClientResponse, status_code=201)
async def register_api_client(
    payload: RegisterAPIClientRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Self-service API key generation.
    Restricted to STL email domains: stl.tech, stltech.in, stldigital.com, stldigitaltech.com
    Returns the plaintext API key **once** — copy it immediately.
    """
    # Check if email already registered
    existing = await db.execute(
        select(APIClient).where(APIClient.email == payload.email, APIClient.is_active == True)
    )
    if existing.scalars().first():
        raise HTTPException(
            status_code=409,
            detail=f"An API client is already registered with email '{payload.email}'. "
                   "Use POST /api/v1/me/rotate-key to rotate your existing key."
        )

    plaintext_key, key_hash, key_prefix = generate_api_key()
    client = APIClient(
        client_name=payload.name,
        client_company=payload.company,
        email=payload.email,
        plan="free",
        api_key_hash=key_hash,
        key_prefix=key_prefix,
        rate_limit_per_minute=FREE_RATE_LIMIT,
        daily_call_quota=FREE_DAILY_QUOTA,
        monthly_call_quota=FREE_MONTHLY_QUOTA,
        is_active=True,
        created_by="self-registered",
    )
    db.add(client)
    await db.commit()
    await db.refresh(client)

    logger.info(f"[PublicAPI] Self-registered API client '{payload.name}' ({payload.email}), ID={client.id}")
    return RegisterAPIClientResponse(
        api_key=plaintext_key,
        key_prefix=key_prefix,
        client_id=client.id,
        email=payload.email,
        plan="free",
        daily_quota=FREE_DAILY_QUOTA,
        monthly_quota=FREE_MONTHLY_QUOTA,
        message=(
            "API key created. Copy it now — it won't be shown again. "
            "Use it as: Authorization: Bearer <key>"
        ),
    )


# ─── Me ───────────────────────────────────────────────────────────────────────

@public_api_router.get("/me", response_model=MeResponse)
async def get_my_profile(client: APIClient = Depends(verify_api_key)):
    """Return your API client profile and current quota usage."""
    return MeResponse(
        client_id=client.id,
        name=client.client_name,
        company=client.client_company,
        email=client.email,
        plan=client.plan or "free",
        key_prefix=client.key_prefix,
        is_active=client.is_active,
        rate_limit_per_minute=client.rate_limit_per_minute,
        daily_call_quota=client.daily_call_quota,
        monthly_call_quota=client.monthly_call_quota,
        calls_today=client.calls_today,
        calls_this_month=client.calls_this_month,
        daily_remaining=max(0, client.daily_call_quota - client.calls_today),
        monthly_remaining=max(0, client.monthly_call_quota - client.calls_this_month),
        created_at=client.created_at.isoformat() if client.created_at else None,
    )


@public_api_router.post("/me/rotate-key", response_model=RotateKeyResponse)
async def rotate_my_key(
    client: APIClient = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Rotate your API key. The old key is invalidated immediately."""
    plaintext_key, key_hash, key_prefix = generate_api_key()
    client.api_key_hash = key_hash
    client.key_prefix = key_prefix
    await db.commit()
    logger.info(f"[PublicAPI] Self-service key rotation for client '{client.client_name}' (ID: {client.id})")
    return RotateKeyResponse(
        api_key=plaintext_key,
        key_prefix=key_prefix,
        message="New API key generated. The old key is now invalid. Copy the new key — it won't be shown again.",
    )


# ─── Chat / Conversation ──────────────────────────────────────────────────────

@public_api_router.post("/chat/sessions", response_model=SessionResponse, status_code=201)
async def create_chat_session(
    payload: CreateSessionRequest,
    client: APIClient = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Create a new named chat session for multi-turn conversation tracking."""
    session_id = payload.session_id or f"api-{client.id}-{uuid.uuid4().hex[:12]}"
    # Check not already existing
    existing = await db.execute(select(ChatSession).where(ChatSession.session_id == session_id))
    if existing.scalars().first():
        raise HTTPException(status_code=409, detail=f"Session '{session_id}' already exists")

    session = ChatSession(
        session_id=session_id,
        api_client_id=client.id,
        workflow_id=payload.workflow_id,
        turn_count=0,
        history_json=[],
        metadata_json=payload.metadata,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    d = session.to_dict()
    return SessionResponse(
        session_id=d["session_id"],
        workflow_id=d["workflow_id"],
        turn_count=d["turn_count"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
        history=d["history"],
    )


@public_api_router.get("/chat/sessions/{session_id}", response_model=SessionResponse)
async def get_chat_session(
    session_id: str,
    client: APIClient = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve full conversation history for a session."""
    stmt = select(ChatSession).where(
        ChatSession.session_id == session_id,
        ChatSession.api_client_id == client.id,
    )
    result = await db.execute(stmt)
    session = result.scalars().first()
    if not session:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    d = session.to_dict()
    return SessionResponse(
        session_id=d["session_id"],
        workflow_id=d["workflow_id"],
        turn_count=d["turn_count"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
        history=d["history"],
    )


@public_api_router.post("/chat", response_model=ChatResponse)
async def chat_turn(
    payload: ChatRequest,
    background_tasks: BackgroundTasks,
    client: APIClient = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """
    Send a text message to the AI voice agent and get a reply.
    No phone call is placed. If session_id is provided, history is automatically
    tracked across turns. Omit session_id for a stateless single-turn exchange.
    """
    from .ai_services import LLMEngineFactory
    from .agent_router import AgentRouter
    from .tool_executor import ToolExecutor
    from .rag_engine import RAGEngine
    from .config import get_config

    cfg = get_config()
    llm = LLMEngineFactory.get_engine(cfg)
    tool_exec = ToolExecutor()
    rag = RAGEngine()
    router_engine = AgentRouter(llm=llm, tool_executor=tool_exec, rag_engine=rag, config=cfg)

    # Resolve session
    session: Optional[ChatSession] = None
    session_id = payload.session_id
    history = []
    turn_number = 1

    if session_id:
        stmt = select(ChatSession).where(
            ChatSession.session_id == session_id,
            ChatSession.api_client_id == client.id,
        )
        result = await db.execute(stmt)
        session = result.scalars().first()
        if not session:
            # Auto-create session
            session = ChatSession(
                session_id=session_id,
                api_client_id=client.id,
                workflow_id=payload.workflow_id,
                turn_count=0,
                history_json=[],
            )
            db.add(session)
            await db.flush()
        history = session.history_json or []
        turn_number = session.turn_count + 1
    else:
        session_id = f"api-stateless-{client.id}-{uuid.uuid4().hex[:8]}"
        # Use explicitly provided history if any
        history = [{"role": m.role, "content": m.content} for m in (payload.history or [])]

    # RAG context
    rag_context = ""
    try:
        rag_context = await rag.search(payload.message)
    except Exception:
        pass

    # Agent routing
    response = await router_engine.route_and_respond(
        session_id=session_id,
        user_message=payload.message,
        history=history,
        workflow_id=payload.workflow_id,
    )

    reply_text = response.get("text", "")
    agent_name = response.get("agent") or response.get("current_agent")
    node_id = response.get("node_id") or response.get("current_node_id")
    tool_calls = response.get("tool_calls")

    # Persist session history
    if session:
        new_history = list(history)
        new_history.append({"role": "user", "content": payload.message})
        new_history.append({"role": "assistant", "content": reply_text})
        session.history_json = new_history
        session.turn_count = turn_number
        await db.commit()

    # Fire webhook in background
    background_tasks.add_task(
        _fire_webhooks, db, client, "chat.turn",
        {"session_id": session_id, "user": payload.message, "reply": reply_text, "turn": turn_number}
    )

    return ChatResponse(
        session_id=session_id,
        reply=reply_text,
        agent=agent_name,
        node_id=node_id,
        tool_calls=tool_calls,
        rag_context=rag_context if rag_context and rag_context != "No relevant context found." else None,
        turn=turn_number,
    )


# ─── TTS ──────────────────────────────────────────────────────────────────────

@public_api_router.post("/tts/synthesize", response_model=TTSSynthesizeResponse)
async def tts_synthesize(
    payload: TTSSynthesizeRequest,
    client: APIClient = Depends(verify_api_key),
):
    """Convert text to speech. Returns base64-encoded audio (MP3 or WAV)."""
    import base64
    from .ai_services import TTSEngineFactory
    from .config import get_config

    cfg = get_config()
    tts = TTSEngineFactory.get_engine(cfg)
    if not tts:
        raise HTTPException(status_code=503, detail="TTS engine is not configured")

    fmt = payload.format.lower() if payload.format else "mp3"
    if fmt not in ("mp3", "wav"):
        raise HTTPException(status_code=400, detail="format must be 'mp3' or 'wav'")

    try:
        audio_bytes = await tts.synthesize_bytes(text=payload.text, format=fmt)
    except Exception as exc:
        logger.error(f"[PublicAPI TTS] Synthesis error: {exc}", exc_info=True)
        raise HTTPException(status_code=502, detail=f"TTS synthesis failed: {exc}")

    if not audio_bytes:
        raise HTTPException(status_code=502, detail="TTS returned empty audio")

    detected_fmt = "wav" if audio_bytes.startswith(b"RIFF") else "mp3"
    b64 = base64.b64encode(audio_bytes).decode("utf-8")
    return TTSSynthesizeResponse(
        audio_base64=f"data:audio/{detected_fmt};base64,{b64}",
        format=detected_fmt,
        voice=payload.voice or "Aarohi",
        character_count=len(payload.text),
    )


@public_api_router.get("/tts/voices", response_model=List[VoiceInfo])
async def list_tts_voices(_client: APIClient = Depends(verify_api_key)):
    """List available TTS voices."""
    return [
        VoiceInfo(name="Aarohi", language="hi-IN", gender="female", provider="cartesia"),
        VoiceInfo(name="Diya",   language="hi-IN", gender="female", provider="sarvam"),
        VoiceInfo(name="Aryan",  language="hi-IN", gender="male",   provider="sarvam"),
        VoiceInfo(name="Meera",  language="hi-IN", gender="female", provider="sarvam"),
    ]


# ─── Outbound Call (gap endpoints) ────────────────────────────────────────────

@public_api_router.get("/outbound/calls", response_model=ListCallsResponse)
async def list_my_calls(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None, description="Filter by status: active / completed / failed / escalated"),
    client: APIClient = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """List all outbound calls triggered by your API key."""
    stmt = select(Call).where(Call.api_client_id == client.id)
    if status:
        stmt = stmt.where(Call.status == status)
    count_stmt = select(func.count(Call.id)).where(Call.api_client_id == client.id)
    if status:
        count_stmt = count_stmt.where(Call.status == status)
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = stmt.order_by(Call.started_at.desc()).offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(stmt)).scalars().all()
    return ListCallsResponse(
        calls=[r.to_dict() for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@public_api_router.delete("/outbound/calls/{call_id}", status_code=204)
async def cancel_call(
    call_id: str,
    client: APIClient = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Cancel a queued or active outbound call. Only works for calls owned by your API key."""
    stmt = select(Call).where(Call.api_client_id == client.id)
    if call_id.isdigit():
        stmt = stmt.where((Call.id == int(call_id)) | (Call.call_sid == call_id))
    else:
        stmt = stmt.where(Call.call_sid == call_id)
    result = await db.execute(stmt)
    call = result.scalars().first()
    if not call:
        raise HTTPException(status_code=404, detail=f"Call '{call_id}' not found")
    if call.status not in (CallStatus.ACTIVE, "queued"):
        raise HTTPException(status_code=409, detail=f"Call is already in status '{call.status}' and cannot be cancelled")
    call.status = CallStatus.FAILED
    call.live_status = "cancelled"
    call.end_reason = "cancelled_via_api"
    call.ended_at = datetime.utcnow()
    await db.commit()


# ─── Tickets ──────────────────────────────────────────────────────────────────

@public_api_router.post("/tickets", response_model=TicketResponse, status_code=201)
async def create_ticket(
    payload: CreateTicketRequest,
    background_tasks: BackgroundTasks,
    client: APIClient = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Create an IT support ticket programmatically via API."""
    ticket_id = f"TKT-{uuid.uuid4().hex[:8].upper()}"
    ticket = Ticket(
        ticket_id=ticket_id,
        call_id=payload.call_id,
        caller_number=payload.caller_number,
        intent=payload.intent,
        issue=payload.issue,
        priority=payload.priority,
        transcript_summary=payload.transcript_summary or "",
        sentiment=payload.sentiment,
    )
    db.add(ticket)
    await db.commit()
    await db.refresh(ticket)

    background_tasks.add_task(
        _fire_webhooks, db, client, "ticket.created", ticket.to_dict()
    )
    return TicketResponse(**ticket.to_dict())


@public_api_router.get("/tickets", response_model=List[TicketResponse])
async def list_tickets(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    priority: Optional[str] = Query(None),
    client: APIClient = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """List tickets. Only tickets created via the API (no call_id filter on client since Ticket has no api_client_id yet; returns global list)."""
    stmt = select(Ticket)
    if priority:
        stmt = stmt.where(Ticket.priority == priority)
    stmt = stmt.order_by(Ticket.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    rows = (await db.execute(stmt)).scalars().all()
    return [TicketResponse(**r.to_dict()) for r in rows]


@public_api_router.get("/tickets/{ticket_id}", response_model=TicketResponse)
async def get_ticket(
    ticket_id: str,
    _client: APIClient = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Fetch a single ticket by its ticket_id (e.g. TKT-A1B2C3D4)."""
    result = await db.execute(select(Ticket).where(Ticket.ticket_id == ticket_id))
    ticket = result.scalars().first()
    if not ticket:
        raise HTTPException(status_code=404, detail=f"Ticket '{ticket_id}' not found")
    return TicketResponse(**ticket.to_dict())


@public_api_router.patch("/tickets/{ticket_id}", response_model=TicketResponse)
async def update_ticket(
    ticket_id: str,
    payload: UpdateTicketRequest,
    background_tasks: BackgroundTasks,
    client: APIClient = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Update a ticket's priority, issue text, or sentiment."""
    result = await db.execute(select(Ticket).where(Ticket.ticket_id == ticket_id))
    ticket = result.scalars().first()
    if not ticket:
        raise HTTPException(status_code=404, detail=f"Ticket '{ticket_id}' not found")
    if payload.priority is not None:
        ticket.priority = payload.priority
    if payload.issue is not None:
        ticket.issue = payload.issue
    if payload.transcript_summary is not None:
        ticket.transcript_summary = payload.transcript_summary
    if payload.sentiment is not None:
        ticket.sentiment = payload.sentiment
    await db.commit()
    await db.refresh(ticket)

    background_tasks.add_task(
        _fire_webhooks, db, client, "ticket.updated", ticket.to_dict()
    )
    return TicketResponse(**ticket.to_dict())


# ─── Analytics ────────────────────────────────────────────────────────────────

@public_api_router.get("/analytics/calls", response_model=AnalyticsResponse)
async def get_analytics(
    days: int = Query(30, ge=1, le=365),
    client: APIClient = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Get call analytics scoped to your API key."""
    since = datetime.utcnow() - timedelta(days=days)
    base = and_(Call.api_client_id == client.id, Call.started_at >= since)

    total       = (await db.execute(select(func.count(Call.id)).where(base))).scalar() or 0
    completed   = (await db.execute(select(func.count(Call.id)).where(and_(base, Call.status == CallStatus.COMPLETED)))).scalar() or 0
    escalated   = (await db.execute(select(func.count(Call.id)).where(and_(base, Call.is_escalated == True)))).scalar() or 0
    transferred = (await db.execute(select(func.count(Call.id)).where(and_(base, Call.transferred_to_human == True)))).scalar() or 0
    active      = (await db.execute(select(func.count(Call.id)).where(and_(Call.api_client_id == client.id, Call.status == CallStatus.ACTIVE)))).scalar() or 0
    avg_dur     = (await db.execute(select(func.avg(Call.duration_seconds)).where(and_(base, Call.duration_seconds.isnot(None))))).scalar() or 0
    ai_resolved = max(0, completed - escalated)

    return AnalyticsResponse(
        period_days=days,
        total_calls=total,
        active_calls=active,
        completed_calls=completed,
        ai_resolved_calls=ai_resolved,
        escalated_calls=escalated,
        transferred_calls=transferred,
        transfer_rate_percent=round(escalated / total * 100, 1) if total else 0.0,
        average_duration_seconds=round(float(avg_dur), 1),
    )


# ─── Webhooks ─────────────────────────────────────────────────────────────────

@public_api_router.post("/webhooks", response_model=WebhookResponse, status_code=201)
async def create_webhook(
    payload: CreateWebhookRequest,
    client: APIClient = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Register a webhook URL to receive events from the Voice Agent platform."""
    ep = WebhookEndpoint(
        api_client_id=client.id,
        url=payload.url,
        events=payload.events,
        secret=payload.secret,
        is_active=True,
    )
    db.add(ep)
    await db.commit()
    await db.refresh(ep)
    return WebhookResponse(**ep.to_dict())


@public_api_router.get("/webhooks", response_model=List[WebhookResponse])
async def list_webhooks(
    client: APIClient = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """List all registered webhook endpoints for your API key."""
    stmt = select(WebhookEndpoint).where(WebhookEndpoint.api_client_id == client.id)
    result = await db.execute(stmt)
    return [WebhookResponse(**ep.to_dict()) for ep in result.scalars().all()]


@public_api_router.delete("/webhooks/{webhook_id}", status_code=204)
async def delete_webhook(
    webhook_id: int,
    client: APIClient = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Remove a registered webhook endpoint."""
    stmt = select(WebhookEndpoint).where(
        WebhookEndpoint.id == webhook_id,
        WebhookEndpoint.api_client_id == client.id,
    )
    result = await db.execute(stmt)
    ep = result.scalars().first()
    if not ep:
        raise HTTPException(status_code=404, detail=f"Webhook {webhook_id} not found")
    await db.delete(ep)
    await db.commit()


# ─── Jev AI System 1 Decisions ────────────────────────────────────────────────

@public_api_router.post("/decision/jev", response_model=JevDecisionResponse)
async def evaluate_jev_decision(
    payload: JevDecisionRequest,
    client: APIClient = Depends(verify_api_key),
):
    """
    Evaluate Jev AI (TypeSafe AI) System 1 decision primitives:
    Choice (categorical selection), Noul (binary probability gate), and Score (numeric rating).
    Falls back gracefully if Jev API key is unconfigured or upstream is unavailable.
    """
    engine = JevDecisionEngine()
    result = await engine.decide(payload.state, payload.questions)
    return JevDecisionResponse(
        model=result.get("model", engine.model),
        answers=result.get("answers", {}),
        fallback=result.get("fallback", False),
        error=result.get("error")
    )

