"""
KYC HTTP API.

public_router  (no Firebase — the patient opens a link on their phone):
    GET  /api/kyc/config
    POST /api/kyc/exchange               {code}   -> session token + liveness plan
    GET  /api/kyc/s/{token}
    POST /api/kyc/s/{token}/consent
    POST /api/kyc/s/{token}/aadhaar      image    -> verify UIDAI-signed Secure QR
    POST /api/kyc/s/{token}/selfie       frames   -> liveness + face match
    POST /api/kyc/demo/credential        (demo mode only)
admin_router   (dashboard, Firebase-protected):
    GET  /api/kyc/call/{call_id}
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from typing import Dict, List

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from . import kyc_core
from .database import get_db

logger = logging.getLogger(__name__)
public_router = APIRouter(prefix="/api/kyc", tags=["kyc"])
admin_router = APIRouter(prefix="/api/kyc", tags=["kyc-admin"])

MAX_IMG_BYTES = 12 * 1024 * 1024
MAX_FRAME_BYTES = 2 * 1024 * 1024
ALLOWED_PHASES = {"straight", "swing", "extra"}
_hits: Dict[str, List[float]] = {}


def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return fwd or (request.client.host if request.client else "unknown")


def _limit(key: str, max_n: int, window_s: int) -> None:
    now = time.time()
    arr = [t for t in _hits.get(key, []) if now - t < window_s]
    if len(arr) >= max_n:
        raise HTTPException(status_code=429, detail="Too many attempts. Please wait a few minutes and try again.")
    arr.append(now)
    _hits[key] = arr


async def _session(db: AsyncSession, token: str):
    s = await kyc_core.load_by_token(db, token)
    if not s:
        raise HTTPException(status_code=404, detail="Verification session not found.")
    if s.status not in kyc_core.ACTIVE:
        if s.status == "verified":
            raise HTTPException(status_code=409, detail="This verification is already complete.")
        raise HTTPException(status_code=410, detail="This verification session has ended. Ask the receptionist for a new code.")
    return s


@public_router.get("/config")
async def kyc_config():
    return {"enabled": await kyc_core.kyc_available(),
            "demo_mode": os.getenv("KYC_DEMO_MODE", "false").lower() == "true"}


class ExchangeIn(BaseModel):
    code: str


@public_router.post("/exchange")
async def exchange(body: ExchangeIn, request: Request):
    _limit(f"ex:{_ip(request)}", 10, 600)  # brute-force guard on the 6-digit code
    data = await kyc_core.exchange_code(body.code)
    if not data:
        raise HTTPException(status_code=404, detail="That code isn't valid or has expired. Ask the receptionist for a new one.")
    return data


@public_router.get("/s/{token}")
async def get_session(token: str, db: AsyncSession = Depends(get_db)):
    s = await kyc_core.load_by_token(db, token)
    if not s:
        raise HTTPException(status_code=404, detail="Verification session not found.")
    out = kyc_core.session_public(s)
    out["final_status"] = s.status
    out["failure_reason"] = s.failure_reason
    return out


@public_router.post("/s/{token}/consent")
async def give_consent(token: str, request: Request, db: AsyncSession = Depends(get_db)):
    s = await _session(db, token)
    s.consent_at = datetime.utcnow()
    s.consent_ip = _ip(request)[:64]
    s.consent_ua = request.headers.get("user-agent", "")[:250]
    if s.status == "pending":
        s.status = "consented"
    await db.commit()
    return kyc_core.session_public(s)


@public_router.post("/s/{token}/aadhaar")
async def verify_aadhaar(token: str, request: Request, image: UploadFile = File(...),
                         password: str = Form(""), db: AsyncSession = Depends(get_db)):
    _limit(f"qr:{_ip(request)}", 25, 600)
    s = await _session(db, token)
    if s.consent_at is None:
        raise HTTPException(status_code=403, detail="Please accept the consent notice first.")
    if (s.attempts_qr or 0) >= kyc_core.MAX_QR_ATTEMPTS:
        raise HTTPException(status_code=410, detail="Too many attempts for this session.")
    raw = await image.read()
    if not raw or len(raw) > MAX_IMG_BYTES:
        raise HTTPException(status_code=413, detail="Image is empty or too large (max 12 MB).")
    sid, qr_left = s.id, max(0, kyc_core.MAX_QR_ATTEMPTS - (s.attempts_qr or 0))
    await db.rollback()  # release the request's DB session/lock during the slow ML call
    try:
        result = await kyc_core.ml_aadhaar(raw, image.filename or "id.jpg", password[:32])
    except Exception as exc:
        logger.error("[KYC] ML aadhaar call failed: %s", exc)
        return JSONResponse(status_code=503, content={
            "ok": False, "reason": "service_unavailable", "message": kyc_core.QR_MESSAGES["service_unavailable"],
            "attempts_left": qr_left})
    return await kyc_core.apply_qr_result(sid, result)


@public_router.post("/s/{token}/selfie")
async def verify_selfie(token: str, request: Request, meta: str = Form(...),
                        frames: List[UploadFile] = File(...), db: AsyncSession = Depends(get_db)):
    _limit(f"sf:{_ip(request)}", 10, 600)
    s = await _session(db, token)
    if s.status != "qr_verified" or not s.id_embedding:
        raise HTTPException(status_code=409, detail="Please complete the Aadhaar QR step first.")
    if (s.attempts_selfie or 0) >= kyc_core.MAX_SELFIE_ATTEMPTS:
        raise HTTPException(status_code=410, detail="Too many attempts for this session.")
    try:
        phases = json.loads(meta)["phases"]
    except Exception:
        raise HTTPException(status_code=400, detail="Bad request.")
    if not isinstance(phases, list) or len(phases) != len(frames) or not (6 <= len(phases) <= 32) \
            or any(p not in ALLOWED_PHASES for p in phases):
        raise HTTPException(status_code=400, detail="Bad request.")
    blobs, total = [], 0
    for f in frames:
        b = await f.read()
        total += len(b)
        if not b or len(b) > MAX_FRAME_BYTES or total > 30 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Frame too large.")
        blobs.append((f.filename or "f.jpg", b))
    payload = {"phases": phases, "extra": s.extra_challenge, "id_embedding": json.loads(s.id_embedding)}
    sid, sf_left = s.id, max(0, kyc_core.MAX_SELFIE_ATTEMPTS - (s.attempts_selfie or 0))
    await db.rollback()  # release the request's DB session/lock during the slow ML call
    try:
        result = await kyc_core.ml_liveness(payload, blobs)
    except Exception as exc:
        logger.error("[KYC] ML liveness call failed: %s", exc)
        return JSONResponse(status_code=503, content={
            "ok": False, "reasons": ["service_unavailable"], "messages": [kyc_core.LIVENESS_MESSAGES["service_unavailable"]],
            "attempts_left": sf_left})
    return await kyc_core.apply_liveness_result(sid, result)


@public_router.post("/demo/credential")
async def demo_credential(request: Request, name: str = Form(...), dob: str = Form("01-01-1990"),
                          gender: str = Form("M"), photo: UploadFile = File(...)):
    if os.getenv("KYC_DEMO_MODE", "false").lower() != "true":
        raise HTTPException(status_code=404, detail="Not found")
    _limit(f"demo:{_ip(request)}", 8, 3600)
    raw = await photo.read()
    if not raw or len(raw) > MAX_IMG_BYTES:
        raise HTTPException(status_code=413, detail="Photo is empty or too large.")
    try:
        status, content, ctype = await kyc_core.ml_demo_credential(name, dob, gender, raw, photo.filename or "p.jpg")
    except Exception as exc:
        logger.error("[KYC] demo credential failed: %s", exc)
        raise HTTPException(status_code=503, detail="Demo generator unavailable.")
    if status == 200:
        return Response(content=content, media_type="image/png")
    try:
        return JSONResponse(status_code=status, content=json.loads(content))
    except Exception:
        raise HTTPException(status_code=status, detail="Could not generate a demo credential.")


@admin_router.get("/call/{call_id}")
async def kyc_for_call(call_id: int):
    return await kyc_core.status_for_call(call_id)
