"""What the person is doing, from a few still frames (Gemini), mapped to a population heart-rate range.

Only 8 downscaled frames are sent, never the video, and the answer is cached per video so a capped key is not hit
twice. The ranges are rough population figures (adult resting 60-100 bpm per the AHA, widened to 45 for fit people),
used only to throw out estimates that are implausible for that situation, never to produce a reading."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.request

import cv2
import numpy as np

from usage_log import record as record_usage

MODELS =["gemini-pro-latest", "gemini-3.8-flash", "gemini-2.5-flash"]
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
N_FRAMES, MAX_SIDE = 8, 512

# (low, high, typical) bpm for an adult; children run higher (see AGE_SHIFT).
RANGES = {
    ("lying", "resting"): (40, 95, 65),
    ("sitting", "resting"): (45, 100, 72),
    ("standing", "resting"): (50, 110, 80),
    ("sitting", "talking"): (50, 110, 78),
    ("standing", "talking"): (55, 115, 84),
    ("any", "moving"): (60, 140, 95),
    ("any", "just_exercised"): (70, 170, 110),
}
UNKNOWN = (40, 180, 75)
AGE_SHIFT = {"child": 15, "teen": 5, "adult": 0, "older_adult": 0}

PROMPT = """These are frames spread across a short selfie-style video recorded to measure heart rate.
Describe only what is visible. Answer in JSON:
- posture: one of sitting, standing, lying, walking, other
- activity: one of resting (still, calm), talking, moving (walking, fidgeting a lot), just_exercised (visibly out of breath, sweating, flushed), other
- age_group: one of child, teen, adult, older_adult
- signs: short list of visible cues you used (e.g. "seated against a backrest", "mouth closed", "steady breathing")
- confidence: low, medium or high
Do not estimate heart rate or any health value."""

SCHEMA = {"type": "OBJECT", "properties": {
    "posture": {"type": "STRING", "enum": ["sitting", "standing", "lying", "walking", "other"]},
    "activity": {"type": "STRING", "enum": ["resting", "talking", "moving", "just_exercised", "other"]},
    "age_group": {"type": "STRING", "enum": ["child", "teen", "adult", "older_adult"]},
    "signs": {"type": "ARRAY", "items": {"type": "STRING"}},
    "confidence": {"type": "STRING", "enum": ["low", "medium", "high"]}},
    "required": ["posture", "activity", "age_group", "confidence"]}


def _frames(path: str) -> list[str]:
    cap = cv2.VideoCapture(path)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    out = []
    for i in np.linspace(0, n - 1, N_FRAMES).astype(int):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok, f = cap.read()
        if not ok:
            continue
        h, w = f.shape[:2]
        s = MAX_SIDE / max(h, w)
        f = cv2.resize(f, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA) if s < 1 else f
        ok, jpg = cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            out.append(base64.b64encode(jpg.tobytes()).decode())
    cap.release()
    return out


def _cache_path(path: str) -> str:
    st = os.stat(path)
    h = hashlib.sha1(f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()[:16]
    return os.path.join(CACHE_DIR, f"context_{h}.json")


def _ask(model: str, key: str, frames: list[str]) -> dict:
    body = {"contents": [{"parts": [{"text": PROMPT}] + [{"inline_data": {"mime_type": "image/jpeg", "data": b}} for b in frames]}],
            "generationConfig": {"responseMimeType": "application/json", "responseSchema": SCHEMA, "temperature": 0}}
    req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                                 data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "x-goog-api-key": key})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.load(r)
    record_usage("vitals_context", model, data.get("usageMetadata"), time.time() - t0)
    text = data["candidates"][0]["content"]["parts"][-1]["text"]
    return json.loads(text)


def describe(path: str, key: str | None = None) -> dict:
    """Gemini's view of posture/activity, or {'error': ...}. Cached per video file."""
    cp = _cache_path(path)
    if os.path.exists(cp):
        with open(cp, encoding="utf-8") as f:
            return json.load(f)
    key = key or os.environ.get("GEMINI_API_KEY")
    if not key:
        return {"error": "no GEMINI_API_KEY set"}
    frames = _frames(path)
    if not frames:
        return {"error": "could not read frames"}
    errors = []
    for model in MODELS:
        try:
            ctx = _ask(model, key, frames)
            ctx["model"] = model
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(cp, "w", encoding="utf-8") as f:
                json.dump(ctx, f, indent=2)
            return ctx
        except urllib.error.HTTPError as e:
            errors.append(f"{model}: HTTP {e.code}")
        except Exception as e:  # noqa: BLE001 - try the next model
            errors.append(f"{model}: {type(e).__name__}")
    return {"error": "; ".join(errors)}


def plausible_range(ctx: dict | None) -> dict:
    """Population heart-rate range for the situation; the wide fallback when the situation is unknown."""
    if not ctx or "error" in ctx:
        lo, hi, typ = UNKNOWN
        return {"low": lo, "high": hi, "typical": typ, "basis": "situation unknown: wide physiological range"}
    posture, activity = ctx.get("posture"), ctx.get("activity")
    if activity in ("moving", "just_exercised") or posture == "walking":
        key = ("any", "just_exercised" if activity == "just_exercised" else "moving")
    else:
        key = (posture if posture in ("lying", "sitting", "standing") else "sitting",
               activity if activity in ("resting", "talking") else "resting")
        if key not in RANGES:
            key = (key[0], "resting")
    lo, hi, typ = RANGES[key]
    shift = AGE_SHIFT.get(ctx.get("age_group"), 0)
    return {"low": lo + shift, "high": hi + shift, "typical": typ + shift,
            "basis": f"{ctx.get('age_group', 'adult')}, {key[0]}, {key[1]} (rough population figures)"}
