"""
Firebase ID token verification for dashboard API/WebSocket routes.
Twilio-facing routes (webhooks, media bridge) are intentionally excluded —
Twilio never carries a Firebase token.

Access is further restricted to an explicit email allow list — Google Sign-In
alone would let any Google account authenticate, which is too broad for an
internal ops dashboard.
"""
import logging
import os
from typing import Optional

import firebase_admin
from firebase_admin import auth as firebase_auth
from fastapi import HTTPException, Request, WebSocket

logger = logging.getLogger(__name__)

# The Firebase project that ISSUES the dashboard sign-in tokens. verify_id_token() needs this to
# check each token's audience/issuer; ADC alone resolves to the GCS service-account project (or None),
# so we pin it explicitly. Override with FIREBASE_PROJECT_ID if the Firebase project ever changes.
FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "voicebot-support")

if not firebase_admin._apps:
    try:
        firebase_admin.initialize_app(options={"projectId": FIREBASE_PROJECT_ID})
    except Exception as _init_exc:  # pragma: no cover
        logging.getLogger(__name__).error("firebase_admin init failed: %s", _init_exc)
        firebase_admin.initialize_app()

ALLOWED_EMAILS = {
    e.strip().lower()
    for e in os.getenv("ALLOWED_AUTH_EMAILS", "").split(",")
    if e.strip()
}

# STL staff domains. A deliberately CLOSED set — public providers (gmail.com etc.) are NOT allowed,
# otherwise "signed in with Google" would mean "anyone on the internet". Grant outside access only
# by adding explicit addresses to ALLOWED_AUTH_EMAILS.
ALLOWED_DOMAINS = {"stl.tech", "stltech.in", "stldigital.com", "stldigitaltech.com"}


def _email_allowed(decoded_token: dict) -> bool:
    email = (decoded_token.get("email") or "").lower().strip()
    if not email:
        return False  # a token with no email is never trusted
    if "*" in ALLOWED_EMAILS:  # explicit, intentional "allow everyone" escape hatch
        return True
    if email in ALLOWED_EMAILS:
        return True
    domain = email.split("@")[-1] if "@" in email else ""
    return domain in ALLOWED_DOMAINS


from .event_logger import event_logger


async def _is_org_member(decoded_token: dict) -> bool:
    """An invited or active organization member may sign in, provided Google has verified their email address."""
    email = (decoded_token.get("email") or "").lower().strip()
    if not email or decoded_token.get("email_verified") is not True:
        return False
    try:
        from sqlalchemy import select
        from .database import async_session
        from .models import TenantMembership
        async with async_session() as db:
            return (await db.execute(select(TenantMembership.id).where(TenantMembership.email == email))).first() is not None
    except Exception as exc:
        logging.getLogger(__name__).warning("membership lookup failed: %s", exc)
        return False


def _strict() -> bool:
    # Secure by default: auth is enforced unless someone explicitly turns it OFF for local dev.
    return os.getenv("REQUIRE_FIREBASE_AUTH", "true").lower() != "false"


async def require_auth(request: Request) -> dict:
    """FastAPI dependency: verifies the Firebase ID token and checks the email allow list.

    No shared/static backdoor token: access requires a real, allow-listed Google identity.
    When auth is disabled (REQUIRE_FIREBASE_AUTH=false, local dev only) everything is allowed."""
    require_strict = _strict()
    authorization = request.headers.get("authorization")
    client_ip = request.client.host if request.client else "unknown"

    if not require_strict:
        return {"email": "localdev@stltech.in", "dev_auth_disabled": True, "is_staff": True}

    if not authorization or not authorization.startswith("Bearer "):
        await event_logger.emit("auth.login_rejected", reason="missing_bearer_token", client_ip=client_ip)
        raise HTTPException(status_code=401, detail="Sign-in required.")

    token = authorization.split(" ", 1)[1]
    try:
        decoded = firebase_auth.verify_id_token(token)
    except Exception as e:
        logger.warning(f"Firebase token verification failed: {e}")
        await event_logger.emit("auth.login_rejected", reason="token_verification_failed", error=str(e), client_ip=client_ip)
        raise HTTPException(status_code=401, detail="Invalid or expired sign-in. Please sign in again.")

    email = decoded.get("email")
    staff = _email_allowed(decoded)
    if not staff and not await _is_org_member(decoded):
        logger.warning(f"Rejected sign-in from non-allow-listed email: {email}")
        await event_logger.emit("auth.login_rejected", email=email, reason="not_in_allowlist", client_ip=client_ip)
        raise HTTPException(status_code=403, detail="This account is not authorized for the dashboard.")

    await event_logger.emit(
        "auth.login_success", email=email, uid=decoded.get("uid"),
        provider=decoded.get("firebase", {}).get("sign_in_provider"), client_ip=client_ip,
    )
    from .tenancy import is_super_admin
    decoded["is_super_admin"] = is_super_admin(email)
    decoded["is_staff"] = staff or decoded["is_super_admin"]        # STL staff, as opposed to a tenant's own users
    return decoded


async def verify_ws_identity(websocket: WebSocket) -> Optional[dict]:
    """Verifies the Firebase ID token passed as a ?token= query param on a WebSocket and returns who it is
    (None when rejected). Same rules as require_auth: a real allow-listed or tenant-member identity."""
    require_strict = _strict()
    token = websocket.query_params.get("token")
    client_ip = websocket.client.host if websocket.client else "unknown"

    if not require_strict:
        return {"email": "localdev@stltech.in", "dev_auth_disabled": True, "is_staff": True}
    if not token:
        await event_logger.emit("auth.login_rejected", type="websocket", reason="missing_token", client_ip=client_ip)
        return None
    try:
        decoded = firebase_auth.verify_id_token(token)
        staff = _email_allowed(decoded)
        allowed = staff or await _is_org_member(decoded)
        if allowed:
            await event_logger.emit("auth.login_success", type="websocket", email=decoded.get("email"), uid=decoded.get("uid"), client_ip=client_ip)
            from .tenancy import is_super_admin
            decoded["is_staff"] = staff or is_super_admin(decoded.get("email"))
            return decoded
        await event_logger.emit("auth.login_rejected", type="websocket", email=decoded.get("email"), reason="not_in_allowlist", client_ip=client_ip)
        return None
    except Exception as e:
        logger.warning(f"WebSocket token verification failed: {e}")
        await event_logger.emit("auth.login_rejected", type="websocket", reason="verification_exception", error=str(e), client_ip=client_ip)
        return None


async def verify_ws_token(websocket: WebSocket) -> bool:
    """True when the WebSocket's token identifies an allowed user (see verify_ws_identity)."""
    return await verify_ws_identity(websocket) is not None
