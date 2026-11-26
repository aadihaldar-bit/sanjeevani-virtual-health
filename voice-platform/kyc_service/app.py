"""
kyc-ml — isolated identity-verification service for the STL voice agent.

Internal only (no published port). Called by the main backend with a shared secret.
Does the heavy / risky work so the live voice backend stays untouched:
  /v1/aadhaar/verify   : decode dense Aadhaar Secure QR, verify UIDAI signature, embed ID photo
  /v1/liveness/verify  : head-pose liveness + face match against the ID photo
  /v1/demo/credential  : (demo mode only) generate a demo-signed QR for a presenter's own photo
We persist NOTHING here: no images, no embeddings.
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import threading
from typing import List, Optional

import numpy as np
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response

import aadhaar as aad
import pdfio
from face import FaceEngine, decode_image, decode_qr_texts, pil_to_bgr
from liveness import CFG, evaluate

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("kyc")

INTERNAL_KEY = os.getenv("KYC_INTERNAL_KEY", "")
DEMO_MODE = os.getenv("KYC_DEMO_MODE", "false").lower() == "true"
CERT_DIR = os.getenv("KYC_CERT_DIR", "/srv/certs")
MAX_IMG = 15 * 1024 * 1024
MAX_FRAMES = 32
ALLOW_UNSIGNED = os.getenv("KYC_ALLOW_UNSIGNED", "true").lower() == "true"   # accept old unsigned QR as low-trust "document" tier

app = FastAPI(title="kyc-ml", docs_url=None, redoc_url=None, openapi_url=None)
engine: Optional[FaceEngine] = None
engine_error: Optional[str] = None
CERTS = aad.load_certs(CERT_DIR)
DEMO_KEY = None
DEMO_PUB = None
if DEMO_MODE:
    import demo as demo_mod
    DEMO_KEY = demo_mod.load_or_create_key()
    DEMO_PUB = DEMO_KEY.public_key()


def _load_engine() -> None:
    global engine, engine_error
    try:
        engine = FaceEngine()
    except Exception as exc:  # pragma: no cover
        engine_error = str(exc)
        log.exception("face engine failed to load")


@app.on_event("startup")
def _startup() -> None:
    threading.Thread(target=_load_engine, daemon=True).start()
    log.info("kyc-ml starting | demo_mode=%s | uidai_certs=%d", DEMO_MODE, len(CERTS))


def auth(x_kyc_key: Optional[str] = Header(default=None)) -> None:
    if not INTERNAL_KEY or not hmac.compare_digest(x_kyc_key or "", INTERNAL_KEY):
        raise HTTPException(status_code=401, detail="unauthorized")


def need_engine() -> FaceEngine:
    if engine is None:
        raise HTTPException(status_code=503, detail=engine_error or "models still loading")
    return engine


@app.get("/healthz")
def healthz():
    return {"ok": True, "models_loaded": engine is not None, "engine_error": engine_error,
            "demo_mode": DEMO_MODE, "uidai_certs": len(CERTS)}


@app.get("/v1/info", dependencies=[Depends(auth)])
def info():
    return {"demo_mode": DEMO_MODE, "uidai_certs": [c[0] for c in CERTS], "thresholds": CFG}


def _read(upload: UploadFile) -> bytes:
    raw = upload.file.read(MAX_IMG + 1)
    if len(raw) > MAX_IMG:
        raise HTTPException(status_code=413, detail="image too large")
    return raw


@app.post("/v1/aadhaar/verify", dependencies=[Depends(auth)])
def aadhaar_verify(image: UploadFile = File(...), password: Optional[str] = Form(None)):
    eng = need_engine()
    raw = _read(image)
    if pdfio.is_pdf(raw):  # e-Aadhaar PDF: render pages, QR is read from the rendered image
        try:
            pages = pdfio.render_pages(raw, (password or "").strip())
        except pdfio.PdfError as exc:
            log.info("aadhaar[0/4] pdf rejected: %s", exc.code)
            return {"ok": False, "reason": exc.code}
        texts, img = [], pages[0]
        for pg in pages:
            texts = decode_qr_texts(pg)
            if any(t.strip().isdigit() and len(t.strip()) >= 500 for t in texts):
                img = pg
                break
        log.info("aadhaar[0/4] pdf pages=%d", len(pages))
    else:
        try:
            img = decode_image(raw)
        except ValueError:
            return {"ok": False, "reason": "unreadable_image"}
        texts = decode_qr_texts(img)
    candidates = [t.strip() for t in texts if t.strip().isdigit() and len(t.strip()) >= 500]
    log.info("aadhaar[1/4] image=%sx%s qr_found=%d numeric_candidates=%d lens=%s non_numeric_lens=%s",
             img.shape[1], img.shape[0], len(texts), len(candidates), [len(c) for c in candidates][:3],
             [len(t) for t in texts if not t.strip().isdigit()][:3])
    if not candidates:
        legacy = next((f for f in (aad.parse_legacy_xml(t) for t in texts) if f), None)
        if legacy and ALLOW_UNSIGNED:
            return _document_tier(eng, img, legacy)
        return {"ok": False, "reason": "qr_not_found" if not texts else "not_secure_qr"}

    last_err = ("not_secure_qr", "")
    for text in candidates:
        try:
            payload = aad.digits_to_payload(text)
            parsed = aad.parse_payload(payload)
        except aad.QrError as exc:
            last_err = (exc.code, exc.message)
            continue

        log.info("aadhaar[2/4] parsed versioned=%s payload=%dB signed=%dB photo=%dB photo_magic=%s fields_filled=%d/%d",
                 parsed.versioned, len(payload), len(parsed.signed), len(parsed.photo),
                 parsed.photo[:4].hex(), sum(1 for v in parsed.fields.values() if v), len(parsed.fields))
        ver = aad.verify_signature(parsed, CERTS, DEMO_PUB)
        log.info("aadhaar[3/4] signature_valid=%s trust=%s signer=%s signer_expired=%s certs_tried=%d",
                 ver.signature_valid, ver.trust, ver.signer, ver.signer_expired, len(CERTS))
        if not ver.signature_valid:
            return {"ok": False, "reason": "signature_invalid"}

        try:  # tolerant decoder: Pillow (JPEG2000) first, OpenCV as fallback
            photo_img = decode_image(parsed.photo)
        except Exception:
            log.info("aadhaar[4/4] id photo could not be decoded")
            return {"ok": False, "reason": "id_photo_unreadable"}
        emb = eng.embed_id_photo(photo_img)
        log.info("aadhaar[4/4] id_photo=%sx%s embedding=%s", photo_img.shape[1], photo_img.shape[0], emb is not None)
        if emb is None:
            return {"ok": False, "reason": "no_face_in_id_photo"}
        return {
            "ok": True,
            "trust": ver.trust,
            "signer": ver.signer,
            "signer_expired": ver.signer_expired,
            "versioned": parsed.versioned,
            "fields": aad.public_summary(parsed),
            "embedding": emb,
        }
    return {"ok": False, "reason": last_err[0]}


def _document_tier(eng: FaceEngine, img: np.ndarray, fields: dict) -> dict:
    """Unsigned (old-format) Aadhaar QR: details are read but NOT cryptographically proven. The ID photo is the
    face printed on the uploaded card / PDF page. Result is labelled trust='document'."""
    with eng._lock:
        face, _ = eng._detect(img)
    if face is None:
        log.info("aadhaar[doc] no face found on the uploaded document")
        return {"ok": False, "reason": "no_face_in_id_photo"}
    x1, y1, x2, y2 = face.bbox
    w, h = x2 - x1, y2 - y1
    H, W = img.shape[:2]
    crop = img[max(0, int(y1 - 0.5 * h)):min(H, int(y2 + 0.4 * h)), max(0, int(x1 - 0.5 * w)):min(W, int(x2 + 0.5 * w))]
    emb = eng.embed_id_photo(crop)
    log.info("aadhaar[doc] unsigned legacy QR accepted as document tier; face=%dx%d embedding=%s", int(w), int(h), emb is not None)
    if emb is None:
        return {"ok": False, "reason": "no_face_in_id_photo"}
    return {"ok": True, "trust": "document", "signer": "unsigned", "signer_expired": False,
            "versioned": False, "fields": fields, "embedding": emb}


@app.post("/v1/liveness/verify", dependencies=[Depends(auth)])
def liveness_verify(meta: str = Form(...), frames: List[UploadFile] = File(...)):
    eng = need_engine()
    try:
        m = json.loads(meta)
        phases: List[str] = m["phases"]
        extra = m.get("extra", "nod")
        id_emb = m.get("id_embedding")
    except Exception:
        raise HTTPException(status_code=400, detail="bad meta")
    if len(frames) > MAX_FRAMES or len(frames) != len(phases):
        raise HTTPException(status_code=400, detail="frame/phase mismatch")

    imgs, metrics = [], []
    for up, ph in zip(frames, phases):
        try:
            img = decode_image(_read(up))
        except ValueError:
            imgs.append(None)
            metrics.append({"phase": ph, "ok": False})
            continue
        imgs.append(img)
        r = eng.analyze(img, embed=(ph == "straight"), fast=True)
        r["phase"] = ph
        metrics.append(r)

    # embed the two most-turned swing frames (one per side) to catch a person swap mid-session
    ok_straight = [x for x in metrics if x.get("phase") == "straight" and x.get("ok")]
    if ok_straight:
        yaw0 = float(np.median([x["yaw"] for x in ok_straight]))
        sw = [(i, x["yaw"] - yaw0) for i, x in enumerate(metrics) if x.get("phase") == "swing" and x.get("ok")]
        picks = []
        if sw:
            picks.append(max(sw, key=lambda t: t[1])[0])
            picks.append(min(sw, key=lambda t: t[1])[0])
        for i in set(picks):
            if imgs[i] is not None:
                r = eng.analyze(imgs[i], embed=True, fast=True)
                if r.get("ok") and "emb" in r:
                    metrics[i]["emb"] = r["emb"]

    result = evaluate(metrics, extra, id_emb)
    # diagnostics for threshold tuning on real cameras — numbers only, no images / no PII
    log.info("liveness verified=%s live=%s matched=%s score=%s reasons=%s detail=%s",
             result["verified"], result["liveness_passed"], result["face_matched"],
             result["match_score"], result["reasons"], result["detail"])
    return result


@app.post("/v1/demo/credential", dependencies=[Depends(auth)])
def demo_credential(name: str = Form(...), dob: str = Form("01-01-1990"), gender: str = Form("M"),
                    photo: UploadFile = File(...)):
    if not DEMO_MODE:
        raise HTTPException(status_code=403, detail="demo mode is off")
    eng = need_engine()
    import demo as demo_mod
    from PIL import Image
    import io

    img = decode_image(_read(photo))
    face, _ = eng._detect(img)
    if face is None:
        return JSONResponse({"ok": False, "reason": "no_face_in_photo"}, status_code=422)
    x1, y1, x2, y2 = face.bbox
    w, h = x2 - x1, y2 - y1
    mx, my = 0.45 * w, 0.55 * h
    H, W = img.shape[:2]
    box = (max(0, int(x1 - mx)), max(0, int(y1 - my)), min(W, int(x2 + mx)), min(H, int(y2 + 0.45 * h)))
    crop = img[box[1]:box[3], box[0]:box[2]]
    rgb = Image.fromarray(crop[:, :, ::-1])
    jp2 = demo_mod.photo_to_jp2(rgb)
    digits = demo_mod.build_digits(DEMO_KEY, name, dob, gender, jp2)
    try:
        png = demo_mod.render_qr_png(digits)
    except ValueError as exc:
        return JSONResponse({"ok": False, "reason": str(exc)}, status_code=422)
    return Response(content=png, media_type="image/png", headers={"X-Digits": str(len(digits))})
