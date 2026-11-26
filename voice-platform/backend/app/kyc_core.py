"""
KYC business logic shared by the HTTP API, the voice tools and the medical flow.

Heavy lifting (QR decode + UIDAI signature check, face match, liveness) lives in the
isolated `kyc-ml` container; this module only orchestrates sessions and talks to it.
Fail-safe: if KYC is not configured or the ML service is down, `kyc_available()` is False
and the voice flow skips KYC rather than blocking bookings.
"""
from __future__ import annotations

import difflib
import json
import logging
import os
import re
import secrets
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import httpx
from sqlalchemy import func, select

from .database import async_session
from .models import KycSession

logger = logging.getLogger(__name__)

ML_URL = os.getenv("KYC_ML_URL", "http://kyc-ml:9000").rstrip("/")
ML_KEY = os.getenv("KYC_INTERNAL_KEY", "")
PUBLIC_URL = os.getenv("KYC_PUBLIC_URL", "https://voicebot-agent.stldigitaltech.com/kyc")
TTL_MIN = int(os.getenv("KYC_CODE_TTL_MIN", "15"))
RETENTION_DAYS = int(os.getenv("KYC_RETENTION_DAYS", "90"))
MAX_SESSIONS_PER_CALL = 3
MAX_QR_ATTEMPTS = 5
MAX_SELFIE_ATTEMPTS = 3
ACTIVE = ("pending", "consented", "qr_verified")

PHASE_PLAN = [
    {"id": "straight", "frames": 4, "seconds": 2.6, "prompt": "Look straight at the camera"},
    {"id": "swing", "frames": 9, "seconds": 5.5, "prompt": "Slowly turn your head to the left, then to the right"},
]
EXTRA_PLAN = {
    "nod": {"id": "extra", "frames": 7, "seconds": 4.0, "prompt": "Now nod your head up and down"},
    "zoom": {"id": "extra", "frames": 7, "seconds": 4.0, "prompt": "Move your face closer to the camera, then back"},
}

QR_MESSAGES = {
    "qr_not_found": "We couldn't find a QR code in that photo. Move closer, keep the QR flat and well lit, and try again.",
    "not_secure_qr": "That QR is an older, unsigned Aadhaar QR and can't be verified. Please upload your e-Aadhaar PDF (free from myaadhaar.uidai.gov.in) or scan the QR in the mAadhaar app.",
    "pdf_password_needed": "This PDF is password protected. Enter the password: the first 4 letters of your name in CAPITALS followed by your birth year (e.g. RAMA1990).",
    "pdf_password_wrong": "That PDF password didn't work. It is the first 4 letters of your name in CAPITALS followed by your birth year, e.g. RAMA1990.",
    "signature_invalid": "This QR could not be verified as a genuine Aadhaar issued by UIDAI.",
    "no_face_in_id_photo": "We couldn't read the photo inside the Aadhaar QR. Please try capturing it again.",
    "id_photo_unreadable": "We couldn't read the photo inside the Aadhaar QR. Please try capturing it again.",
    "unreadable_image": "We couldn't read that image. Please take the photo again.",
    "malformed": "That QR code looks damaged or incomplete. Please try again.",
    "service_unavailable": "Verification is temporarily unavailable. Please try again in a minute.",
}
LIVENESS_MESSAGES = {
    "face_not_clear": "We couldn't see your face clearly. Face the camera in good light.",
    "image_too_blurry": "The video was too blurry. Hold the phone steady and try again.",
    "face_too_small": "Hold the phone a little closer so your face fills the frame.",
    "multiple_faces": "Only one person should be in the frame.",
    "not_facing_camera": "Please look straight at the camera at the start.",
    "no_head_turn": "We didn't see you turn your head to both sides. Turn clearly left, then right.",
    "no_nod": "We didn't see you nod. Move your head clearly up and down.",
    "no_zoom": "We didn't see you move closer. Bring your face nearer, then back.",
    "different_person_in_session": "The person in the video changed. Please complete this yourself.",
    "face_mismatch": "Your face didn't match the photo on the Aadhaar.",
    "service_unavailable": "Verification is temporarily unavailable. Please try again in a minute.",
}


# ───────────────────────── helpers ─────────────────────────
def spoken_code(code: str) -> str:
    return " ".join(code)


def spoken_url() -> str:
    u = re.sub(r"^https?://", "", PUBLIC_URL).rstrip("/")
    return u.replace(".", " dot ").replace("/", " slash ")


def name_similarity(a: str, b: str) -> float:
    def norm(s: str) -> str:
        s = re.sub(r"[^a-z ]", "", (s or "").lower())
        return " ".join(sorted(t for t in s.split() if t))
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    return round(difflib.SequenceMatcher(None, a, b).ratio(), 3)


_health_cache = {"t": 0.0, "ok": False}
_last_purge = {"t": 0.0}


def kyc_configured() -> bool:
    """KYC switched on and wired up (independent of whether the ML service is up right now)."""
    return os.getenv("KYC_ENABLED", "true").lower() == "true" and bool(ML_KEY)


def fail_closed() -> bool:
    """KYC_FAIL_MODE=closed -> if the ML service is down, do NOT book unverified patients."""
    return os.getenv("KYC_FAIL_MODE", "open").lower() == "closed"


def is_simulator_caller(caller: Optional[str]) -> bool:
    """A web-simulator session (not a real phone call)."""
    return (caller or "").strip().lower() in ("web simulator", "simulator")


def kyc_skipped_for_channel(caller: Optional[str]) -> bool:
    """When KYC_SIMULATOR_ONLY=true, KYC engages only for the web simulator and is skipped on real
    phone calls (so the gate can be tested in the simulator without affecting live callers)."""
    simulator_only = os.getenv("KYC_SIMULATOR_ONLY", "false").lower() == "true"
    return simulator_only and not is_simulator_caller(caller)


async def purge_old() -> int:
    """Data minimisation: strip personal fields from sessions older than the retention window
    (keeps the audit skeleton: status, scores, timestamps). Runs at most hourly."""
    now = time.time()
    if now - _last_purge["t"] < 3600:
        return 0
    _last_purge["t"] = now
    cutoff = datetime.utcnow() - timedelta(days=RETENTION_DAYS)
    n = 0
    async with async_session() as db:
        rows = (await db.execute(select(KycSession).where(
            KycSession.created_at < cutoff,
            (KycSession.verified_name.isnot(None)) | (KycSession.claimed_name.isnot(None)) |
            (KycSession.caller_number.isnot(None))))).scalars().all()
        for s in rows:
            s.verified_name = s.birth_year = s.aadhaar_last4 = s.claimed_name = None
            s.consent_ip = s.consent_ua = s.caller_number = s.id_embedding = s.code = None
            n += 1
        if n:
            await db.commit()
    if n:
        logger.info("[KYC] retention purge: stripped personal fields from %d session(s) older than %d days", n, RETENTION_DAYS)
    return n


async def kyc_available() -> bool:
    """Configured AND the ML sidecar answers. Cached for 20 s so voice turns stay fast."""
    if os.getenv("KYC_ENABLED", "true").lower() != "true" or not ML_KEY:
        return False
    now = time.time()
    if now - _health_cache["t"] < 20:
        return _health_cache["ok"]
    ok = False
    try:
        async with httpx.AsyncClient(timeout=2.5) as c:
            r = await c.get(f"{ML_URL}/healthz")
            ok = r.status_code == 200 and bool(r.json().get("models_loaded"))
    except Exception as exc:
        logger.warning("[KYC] ML sidecar not reachable: %s", exc)
    _health_cache.update(t=now, ok=ok)
    return ok


async def _broadcast(summary: dict):
    try:
        from .websocket_manager import dashboard_manager
        await dashboard_manager.broadcast({"type": "kyc_updated", "call_id": summary.get("call_id"), "kyc": summary})
    except Exception as exc:
        logger.debug("[KYC] broadcast skipped: %s", exc)


def _wipe(s: KycSession):
    """Drop sensitive/ephemeral material once a session is finished."""
    s.id_embedding = None
    s.code = None


# ───────────────────────── sessions ─────────────────────────
async def create_session(call_id: Optional[int], caller_number: str = "", claimed_name: str = "") -> Optional[dict]:
    try:
        await purge_old()
    except Exception as exc:  # housekeeping must never block a patient
        logger.warning("[KYC] purge skipped: %s", exc)
    now = datetime.utcnow()
    async with async_session() as db:
        used = (await db.execute(select(func.count(KycSession.id)).where(KycSession.call_id == call_id))).scalar() or 0
        if call_id is not None and used >= MAX_SESSIONS_PER_CALL:
            return None
        # retire any still-open session for this call
        if call_id is not None:
            old = (await db.execute(select(KycSession).where(KycSession.call_id == call_id, KycSession.status.in_(ACTIVE)))).scalars().all()
            for o in old:
                o.status = "expired"
                _wipe(o)
        code = None
        for _ in range(12):
            cand = f"{secrets.randbelow(1_000_000):06d}"
            clash = (await db.execute(select(KycSession.id).where(
                KycSession.code == cand, KycSession.status.in_(ACTIVE), KycSession.expires_at > now))).first()
            if not clash:
                code = cand
                break
        if code is None:
            return None
        s = KycSession(
            code=code, token=secrets.token_urlsafe(32), call_id=call_id, caller_number=caller_number or None,
            claimed_name=(claimed_name or None), status="pending", extra_challenge=secrets.choice(["nod", "zoom"]),
            created_at=now, expires_at=now + timedelta(minutes=TTL_MIN),
        )
        db.add(s)
        await db.commit()
        await db.refresh(s)
        logger.info("[KYC] session %s created for call %s", s.id, call_id)
        return {"id": s.id, "code": code, "sessions_used": used + 1}


async def _expire_if_needed(db, s: KycSession) -> KycSession:
    if s.status in ACTIVE and s.expires_at and s.expires_at < datetime.utcnow():
        s.status = "expired"
        _wipe(s)
        await db.commit()
    return s


async def status_for_call(call_id: Optional[int]) -> dict:
    """What the voice flow needs each turn: state, verified name, whether a retry is allowed."""
    if call_id is None:
        return {"state": "none"}
    async with async_session() as db:
        rows = (await db.execute(select(KycSession).where(KycSession.call_id == call_id).order_by(KycSession.id.desc()))).scalars().all()
        if not rows:
            return {"state": "none", "sessions_used": 0, "can_retry": True}
        s = await _expire_if_needed(db, rows[0])
        return {
            "state": s.status,
            "name": s.verified_name,
            "code": s.code if s.status in ACTIVE else None,
            "sessions_used": len(rows),
            "can_retry": len(rows) < MAX_SESSIONS_PER_CALL,
            "failure_reason": s.failure_reason,
            "summary": s.summary(),
        }


async def summaries_for_calls(call_ids: List[int]) -> Dict[int, dict]:
    """Latest KYC summary per call, for the Health System page."""
    if not call_ids:
        return {}
    out: Dict[int, dict] = {}
    async with async_session() as db:
        rows = (await db.execute(select(KycSession).where(KycSession.call_id.in_(call_ids)).order_by(KycSession.id.asc()))).scalars().all()
        for s in rows:  # ascending -> later rows overwrite earlier ones
            out[s.call_id] = s.summary()
    return out


async def exchange_code(code: str) -> Optional[dict]:
    code = re.sub(r"\D", "", code or "")
    if len(code) != 6:
        return None
    async with async_session() as db:
        s = (await db.execute(select(KycSession).where(KycSession.code == code, KycSession.status.in_(ACTIVE)))).scalars().first()
        if not s:
            return None
        s = await _expire_if_needed(db, s)
        if s.status not in ACTIVE:
            return None
        return session_public(s)


def session_public(s: KycSession) -> dict:
    plan = PHASE_PLAN + [EXTRA_PLAN.get(s.extra_challenge or "nod", EXTRA_PLAN["nod"])]
    demo_mode = os.getenv("KYC_DEMO_MODE", "false").lower() == "true"
    return {
        "token": s.token,
        "status": s.status,
        "expires_in": max(0, int((s.expires_at - datetime.utcnow()).total_seconds())),
        "consent_given": s.consent_at is not None,
        "challenge": {"extra": s.extra_challenge, "phases": plan},
        "qr_attempts_left": max(0, MAX_QR_ATTEMPTS - (s.attempts_qr or 0)),
        "selfie_attempts_left": max(0, MAX_SELFIE_ATTEMPTS - (s.attempts_selfie or 0)),
        "identity": ({"name": s.verified_name, "last4": s.aadhaar_last4, "birth_year": s.birth_year,
                      "demo": s.trust == "demo", "unsigned": s.trust == "document"} if s.status in ("qr_verified", "verified") else None),
        "demo_mode": demo_mode,
    }


async def load_by_token(db, token: str) -> Optional[KycSession]:
    s = (await db.execute(select(KycSession).where(KycSession.token == token))).scalars().first()
    if s:
        await _expire_if_needed(db, s)
    return s


# ───────────────────────── sidecar client ─────────────────────────
def _hdr() -> dict:
    return {"X-KYC-Key": ML_KEY}


async def ml_aadhaar(raw: bytes, filename: str = "id.jpg", password: str = "") -> dict:
    async with httpx.AsyncClient(timeout=httpx.Timeout(75.0)) as c:
        r = await c.post(f"{ML_URL}/v1/aadhaar/verify", headers=_hdr(), files={"image": (filename, raw, "application/octet-stream")},
                         data={"password": password} if password else None)
        r.raise_for_status()
        return r.json()


async def ml_liveness(meta: dict, frames: List[Tuple[str, bytes]]) -> dict:
    files = [("frames", (n or f"f{i}.jpg", b, "image/jpeg")) for i, (n, b) in enumerate(frames)]
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as c:
        r = await c.post(f"{ML_URL}/v1/liveness/verify", headers=_hdr(), data={"meta": json.dumps(meta)}, files=files)
        r.raise_for_status()
        return r.json()


async def ml_demo_credential(name: str, dob: str, gender: str, photo: bytes, photo_name: str = "p.jpg") -> Tuple[int, bytes, str]:
    async with httpx.AsyncClient(timeout=httpx.Timeout(75.0)) as c:
        r = await c.post(f"{ML_URL}/v1/demo/credential", headers=_hdr(),
                         data={"name": name, "dob": dob, "gender": gender},
                         files={"photo": (photo_name, photo, "application/octet-stream")})
        return r.status_code, r.content, r.headers.get("content-type", "")


# ───────────────────────── state transitions ─────────────────────────
async def apply_qr_result(s_id: int, result: dict) -> dict:
    """Persist the outcome of the Aadhaar QR step. Returns the public response."""
    async with async_session() as db:
        s = (await db.execute(select(KycSession).where(KycSession.id == s_id))).scalars().first()
        s.attempts_qr = (s.attempts_qr or 0) + 1
        if result.get("ok"):
            f = result.get("fields", {})
            s.trust = result.get("trust")
            s.signer = result.get("signer")
            s.verified_name = (f.get("name") or "").strip()[:120]
            s.birth_year = (f.get("birth_year") or "")[:4]
            s.gender = (f.get("gender") or "")[:2]
            s.aadhaar_last4 = (f.get("aadhaar_last4") or "")[:4]
            s.id_embedding = json.dumps(result.get("embedding"))
            s.name_match = name_similarity(s.claimed_name or "", s.verified_name or "")
            s.status = "qr_verified"
            s.failure_reason = None
            await db.commit()
            await _broadcast(s.summary())
            return {"ok": True, "identity": {"name": s.verified_name, "last4": s.aadhaar_last4,
                                             "birth_year": s.birth_year, "demo": s.trust == "demo", "unsigned": s.trust == "document"}}
        reason = result.get("reason", "unknown")
        left = max(0, MAX_QR_ATTEMPTS - s.attempts_qr)
        if left == 0:
            s.status, s.failure_reason = "failed", f"qr:{reason}"
            _wipe(s)
        await db.commit()
        if left == 0:
            await _broadcast(s.summary())
        return {"ok": False, "reason": reason, "message": QR_MESSAGES.get(reason, QR_MESSAGES["qr_not_found"]),
                "attempts_left": left, "final": left == 0}


async def apply_liveness_result(s_id: int, result: dict) -> dict:
    async with async_session() as db:
        s = (await db.execute(select(KycSession).where(KycSession.id == s_id))).scalars().first()
        s.attempts_selfie = (s.attempts_selfie or 0) + 1
        s.face_score = result.get("match_score")
        s.liveness_passed = bool(result.get("liveness_passed"))
        if result.get("verified"):
            s.status = "verified"
            s.verified_at = datetime.utcnow()
            s.failure_reason = None
            _wipe(s)
            await db.commit()
            await _broadcast(s.summary())
            return {"ok": True, "face_score": s.face_score, "identity": {"name": s.verified_name, "last4": s.aadhaar_last4,
                                                                       "demo": s.trust == "demo"}}
        reasons = result.get("reasons") or ["no_head_turn"]
        left = max(0, MAX_SELFIE_ATTEMPTS - s.attempts_selfie)
        s.failure_reason = f"selfie:{reasons[0]}"
        if left == 0:
            s.status = "failed"
            _wipe(s)
        await db.commit()
        if left == 0:
            await _broadcast(s.summary())
        return {"ok": False, "reasons": reasons, "messages": [LIVENESS_MESSAGES.get(r, r) for r in reasons[:3]],
                "attempts_left": left, "final": left == 0, "face_score": s.face_score}
