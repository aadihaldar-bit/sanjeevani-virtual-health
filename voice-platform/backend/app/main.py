"""
FastAPI Voice Agent Dashboard Backend
Manages webhook ingestion, REST APIs, and WebSocket broadcasts
for the STL Digital voice agent platform.
"""

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends
from fastapi.middleware.cors import CORSMiddleware
import os
import json
import logging
import asyncio
from datetime import datetime
from contextlib import asynccontextmanager

from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from .database import init_db, get_db, async_session
from .models import Call, CallStatus
from .websocket_manager import dashboard_manager
from .twilio_client import twilio_sync_client
from .webhooks import router as webhook_router
from .api_routes import router as api_router, tickets_router
from .logging_routes import router as logging_router
from .knowledge_routes import router as knowledge_router
from .media_bridge import router as media_bridge_router
from . import workflow_api
from . import stt_api, agents_api
from .outbound_api import outbound_router
from .outbound_api_admin import outbound_admin_router
from .public_api import public_api_router
from . import security_api
from .firebase_auth import require_auth, verify_ws_identity, verify_ws_token
from .tenancy import multi_tenant_enabled, require_staff, resolve_scope, tenant_scope


from .event_logger import event_logger
from fastapi import Request


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# Background task to poll active conversations
twilio_active_calls_task = None


async def poll_twilio_active_calls():
    """Background task: Poll Twilio for active calls every 5 seconds."""
    last_broadcast_count = None

    while True:
        try:
            await asyncio.sleep(5)
            if not twilio_sync_client.is_configured():
                continue

            stats = await twilio_sync_client.sync_active_calls()
            if stats.get("errors", 0) > 0:
                # If Twilio auth failed or errored, back off for 15 seconds
                await asyncio.sleep(10)
                continue

            should_broadcast = (
                stats["upserted"] > 0
                or stats["completed"] > 0
                or last_broadcast_count != stats["active_count"]
            )

            if should_broadcast:
                async with async_session() as db:
                    result = await db.execute(
                        select(Call)
                        .options(selectinload(Call.tickets))
                        .where(Call.status == CallStatus.ACTIVE)
                        .order_by(Call.started_at.desc())
                    )
                    active_calls = result.scalars().all()

                    await dashboard_manager.broadcast({
                        "type": "active_calls_updated",
                        "active_calls": [c.to_dict() for c in active_calls],
                        "count": len(active_calls),
                        "source": "twilio_active_poll",
                    })
                    last_broadcast_count = len(active_calls)
        except Exception as e:
            logger.warning(f"Twilio active call polling paused: {e}")
            await asyncio.sleep(10)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup/shutdown lifecycle."""
    global twilio_active_calls_task
    # Startup
    logger.info("Starting Voice Agent Dashboard Backend...")
    event_logger.start()
    await event_logger.emit(
        "system.startup",
        version="2.0.0",
        telephony=os.getenv("TELEPHONY_PROVIDER", "smartflo"),
        environment=os.getenv("ENVIRONMENT", "production")
    )

    await init_db()
    logger.info("Database initialized")
    from .database import seed_default_workflows
    await seed_default_workflows()
    
    # Start background polling task
    twilio_active_calls_task = asyncio.create_task(poll_twilio_active_calls())
    logger.info("Twilio active call polling task started")
    from .call_reaper import reaper_loop
    call_reaper_task = asyncio.create_task(reaper_loop())

    # Sanjeevani patient-medication follow-up scheduler. Inert unless SANJEEVANI_SCHEDULER_ENABLED=true.
    from .followup_scheduler import followup_worker_loop
    sanjeevani_scheduler_task = asyncio.create_task(followup_worker_loop())

    yield
    
    # Shutdown
    logger.info("Shutting down...")
    await event_logger.emit("system.shutdown")
    if twilio_active_calls_task:
        twilio_active_calls_task.cancel()
    call_reaper_task.cancel()
    sanjeevani_scheduler_task.cancel()
    await event_logger.stop()


app = FastAPI(
    title="Voice Agent Dashboard API",
    description="Backend for AI Voice Agent monitoring dashboard",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS: restricted to the known dashboard/KYC origins. The dashboard and the patient /kyc page are
# same-origin with the API in prod, and server-to-server API clients ignore CORS, so a wildcard only
# widened the browser attack surface. Override with CORS_ORIGINS (comma-separated) if needed.
_default_origins = "https://voicebot-agent.stldigitaltech.com,http://localhost:3000,http://localhost:3001"
_cors_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", _default_origins).split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,  # auth is via Authorization header, never cookies
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


# ─── Security headers (defense-in-depth; TLS is terminated at nginx) ────────────
@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(self), microphone=(self), geolocation=()")
    response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    # API responses can carry patient data — keep them out of shared/proxy caches.
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


# ─── HTTP Request & Error Logging Middleware ───────────────────────────────

@app.middleware("http")
async def event_logger_middleware(request: Request, call_next):
    start_time = asyncio.get_event_loop().time()
    client_ip = request.client.host if request.client else "unknown"
    user_email = None

    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token_str = auth_header.split(" ", 1)[1]
        if token_str == "demo_token":
            user_email = "authorized@stltech.in"

    try:
        response = await call_next(request)
        duration_ms = round((asyncio.get_event_loop().time() - start_time) * 1000, 2)

        # Skip high-frequency health probes or static favicon from flooding logs
        if request.url.path not in ["/health", "/favicon.ico"]:
            await event_logger.emit(
                "http.request",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=duration_ms,
                client_ip=client_ip,
                user_email=user_email
            )
        return response
    except Exception as exc:
        duration_ms = round((asyncio.get_event_loop().time() - start_time) * 1000, 2)
        await event_logger.emit(
            "system.error",
            source="http_middleware",
            path=request.url.path,
            method=request.method,
            duration_ms=duration_ms,
            client_ip=client_ip,
            error=str(exc),
            error_type=type(exc).__name__
        )
        raise


# ─── WebSocket: Dashboard Real-time Stream ──────────────────────────────────

@app.websocket("/dashboard-stream")
async def dashboard_stream_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time dashboard updates.
    On connect: sends current active calls and recent stats.
    Ongoing: receives ping/pong, forwards events from webhooks.
    """
    token = websocket.query_params.get("token")
    identity = await verify_ws_identity(websocket)
    if identity is None:
        await event_logger.emit(
            "auth.login_rejected",
            type="websocket",
            endpoint="/dashboard-stream",
            client_ip=websocket.client.host if websocket.client else "unknown",
            reason="invalid_or_missing_token"
        )
        await websocket.close(code=4401)
        return

    from .tenancy import NO_TENANT, current_scope
    from .database import async_session as _scope_session
    async with _scope_session() as _sdb:
        try:
            conn_scope = await resolve_scope(identity, _sdb, websocket.query_params.get("tenant_id"))
        except Exception:
            await websocket.close(code=4403)
            return
    if not multi_tenant_enabled() and not (identity.get("is_staff") or conn_scope.is_super_admin):
        await websocket.close(code=4403)
        return
    await websocket.accept()
    client_id = str(id(websocket))
    await dashboard_manager.connect(websocket, client_id, tenant_id=conn_scope.tenant_id)
    await event_logger.emit(
        "dashboard.ws_connect",
        client_id=client_id,
        client_ip=websocket.client.host if websocket.client else "unknown",
        token_present=bool(token)
    )

    try:
        logger.info(f"Dashboard client connected: {client_id}")

        # Send current state on connect (limited to this connection's tenant when multi-tenancy is on)
        current_scope.set(conn_scope)
        async with async_session() as db:
            # Active calls
            active_result = await db.execute(
                select(Call)
                .options(selectinload(Call.tickets))
                .where(Call.status == CallStatus.ACTIVE)
                .order_by(Call.started_at.desc())
            )
            active_calls = active_result.scalars().all()

            # Recent completed calls (last 10)
            recent_result = await db.execute(
                select(Call)
                .options(selectinload(Call.tickets))
                .where(Call.status != CallStatus.ACTIVE)
                .order_by(Call.started_at.desc())
                .limit(10)
            )
            recent_calls = recent_result.scalars().all()

            # Quick stats
            total_count = (await db.execute(select(func.count(Call.id)))).scalar() or 0
            escalated_count = (await db.execute(
                select(func.count(Call.id)).where(Call.is_escalated == True)
            )).scalar() or 0

        await websocket.send_text(json.dumps({
            "type": "initial_state",
            "active_calls": [c.to_dict() for c in active_calls],
            "recent_calls": [c.to_dict() for c in recent_calls],
            "stats": {
                "total_calls": total_count,
                "active_calls": len(active_calls),
                "escalated_calls": escalated_count,
            },
            "timestamp": datetime.utcnow().isoformat(),
        }, default=str))

        # Keep connection alive — listen for client messages
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)

            if message.get("type") == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))

    except WebSocketDisconnect:
        logger.info(f"Dashboard client disconnected: {client_id}")
    except Exception as e:
        logger.error(f"Dashboard WebSocket error: {e}")
    finally:
        await event_logger.emit("dashboard.ws_disconnect", client_id=client_id)
        await dashboard_manager.disconnect(client_id=client_id)


# Mount routers
# Twilio-facing routers are left unauthenticated — Twilio never carries a Firebase token.
app.include_router(webhook_router)
app.include_router(media_bridge_router)

# Dashboard-facing routers require a valid Firebase ID token.
_auth_dep = [Depends(require_auth)]
# Routes that serve tenant data: signed in, and limited to the caller's tenant (see tenancy.tenant_scope).
_data_dep = [Depends(require_auth), Depends(tenant_scope)]
# Platform-level routes: STL staff and super admins only.
_staff_dep = [Depends(require_staff)]
app.include_router(api_router, dependencies=_data_dep)
app.include_router(tickets_router, dependencies=_data_dep)
app.include_router(tickets_router, prefix="/api", dependencies=_data_dep)
app.include_router(logging_router, dependencies=_staff_dep)
app.include_router(knowledge_router, dependencies=_data_dep)
app.include_router(workflow_api.router, dependencies=_data_dep)
app.include_router(security_api.router, dependencies=_staff_dep)
app.include_router(stt_api.router, dependencies=_data_dep)          # speech tools: open to everyone signed in (roles apply)
app.include_router(stt_api.tts_router, dependencies=_data_dep)
app.include_router(stt_api.credits_router, dependencies=_data_dep)
app.include_router(agents_api.router, dependencies=_data_dep)
from . import architect_api
app.include_router(architect_api.router, dependencies=_data_dep)      # the console's built-in assistant (administrators)
from . import architect_design
app.include_router(architect_design.router, dependencies=_data_dep)   # ... and its design of an agent: desks, documents, voice
from . import architect_run
app.include_router(architect_run.router, dependencies=_data_dep)      # ... and its work on a request, step by step, as a stream

# Organizations, roles, phone-number allocation, bring-your-own models and external vector stores.
# These carry their own role checks on top of the sign-in requirement.
from . import org_api, numbers_api, external_models_api, vector_stores_api
app.include_router(org_api.router, dependencies=_auth_dep)
app.include_router(numbers_api.router, dependencies=_auth_dep)
app.include_router(external_models_api.router, dependencies=_auth_dep)
app.include_router(vector_stores_api.router, dependencies=_auth_dep)

# External Outbound Voice API (Authenticated via API Key)
app.include_router(outbound_router)

# Admin API Key management (Authenticated via Firebase Auth)
app.include_router(outbound_admin_router, dependencies=_data_dep)

# Patient identity verification (KYC). The patient opens a link on their phone, so the
# patient-facing endpoints carry no Firebase token (they are token + rate-limit + consent
# gated); the dashboard summary endpoint is Firebase-protected.
from .kyc_api import public_router as kyc_public_router, admin_router as kyc_admin_router
app.include_router(kyc_public_router)
from .care_links import public_router as care_public_router      # one-time prescription-upload and payment pages (no sign-in; code-gated)
app.include_router(care_public_router)
app.include_router(kyc_admin_router, dependencies=_data_dep)

# Sanjeevani patient-medication voice agent.
#  - v1 prescription push: API client key auth (like the outbound API), no Firebase.
#  - console + reports: Firebase + tenant scope (role gating via tenancy.role_denial).
from .prescriptions_api import (
    v1_router as sanjeevani_prescriptions_v1_router,
    console_router as sanjeevani_console_router,
)
from .sanjeevani_reports import reports_router as sanjeevani_reports_router
app.include_router(sanjeevani_prescriptions_v1_router)
from .patient_record_api import router as patient_record_router  # noqa: E402  (API-key read of one patient's record)
app.include_router(patient_record_router)
app.include_router(sanjeevani_console_router, dependencies=_data_dep)
app.include_router(sanjeevani_reports_router, dependencies=_data_dep)

# Public Developer API v1 (API key auth, no Firebase required)
# Covers: /api/v1/auth/register, /api/v1/me, /api/v1/chat,
#         /api/v1/tts, /api/v1/outbound/calls, /api/v1/tickets,
#         /api/v1/analytics, /api/v1/webhooks
app.include_router(public_api_router)


# ─── Health Check ───────────────────────────────────────────────────────────

@app.get("/health")
async def health_check():
    """Health check endpoint with database status."""
    db_status = "unknown"
    try:
        async with async_session() as db:
            await db.execute(select(func.count(Call.id)))
            db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"

    return {
        "status": "healthy",
        "database": db_status,
        "dashboard_clients": dashboard_manager.client_count,
        "timestamp": datetime.utcnow().isoformat(),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8055)
