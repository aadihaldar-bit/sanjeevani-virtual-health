"""
Server-side active-liveness evaluation.

The browser streams short bursts of frames while the patient follows prompts:
  straight -> look at the camera
  swing    -> slowly turn the head to BOTH sides
  extra    -> one randomly assigned extra move: "nod" (pitch swing) or "zoom" (move closer, then back)

All checks are direction-agnostic on purpose (we require movement to BOTH sides, a pitch
range, or a face-size change) so they don't depend on camera mirroring conventions.

HONEST LIMITS: this is demo-grade presentation-attack detection. It stops static photos and
simple screen replays that don't move, and it rejects mid-session person swaps. It is NOT a
certified PAD (ISO 30107-3) and is not injection-attack proof (virtual cameras, deepfakes).
Use a certified vendor for regulated production claims.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

import numpy as np


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


CFG = {
    "match_threshold": _f("KYC_MATCH_THRESHOLD", 0.35),     # cosine, ArcFace; tunable, NOT calibrated on Indian IDs
    "consistency_threshold": _f("KYC_CONSISTENCY_THRESHOLD", 0.30),
    "swing_deg": _f("KYC_SWING_DEG", 14.0),
    "nod_deg": _f("KYC_NOD_DEG", 12.0),
    "zoom_ratio": _f("KYC_ZOOM_RATIO", 1.25),
    "min_face_px": _f("KYC_MIN_FACE_PX", 90.0),
    "min_blur": _f("KYC_MIN_BLUR", 18.0),
}


def _valid(frames: List[dict], phase: str) -> List[dict]:
    return [m for m in frames if m.get("phase") == phase and m.get("ok")]


def _cos(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


def evaluate(frames: List[dict], extra: str, id_embedding: Optional[List[float]], cfg: Optional[dict] = None) -> Dict:
    """frames: per-frame metrics {phase, ok, yaw, pitch, width, blur, emb(optional)}."""
    c = {**CFG, **(cfg or {})}
    reasons: List[str] = []
    detail: Dict = {"frames_total": len(frames), "frames_valid": sum(1 for m in frames if m.get("ok"))}

    straight = _valid(frames, "straight")
    swing = _valid(frames, "swing")
    extra_frames = _valid(frames, "extra")
    detail.update(straight=len(straight), swing=len(swing), extra=len(extra_frames), extra_challenge=extra)

    # ---- quality gates ----
    if len(straight) < 3:
        reasons.append("face_not_clear")  # couldn't see one clear, frontal face
    quality = [m for m in frames if m.get("ok")]
    if quality:
        blur = float(np.median([m.get("blur", 0) for m in quality]))
        detail["median_blur"] = round(blur, 1)
        if blur < c["min_blur"]:
            reasons.append("image_too_blurry")
        if float(np.median([m.get("width", 0) for m in quality])) < c["min_face_px"]:
            reasons.append("face_too_small")
    if any(m.get("multi_face") for m in quality):
        reasons.append("multiple_faces")

    # ---- frontal baseline ----
    yaw0 = pitch0 = 0.0
    if straight:
        yaw0 = float(np.median([m["yaw"] for m in straight]))
        pitch0 = float(np.median([m["pitch"] for m in straight]))
        detail.update(baseline_yaw=round(yaw0, 1), baseline_pitch=round(pitch0, 1))
        if abs(yaw0) > 22 or abs(pitch0) > 28:
            reasons.append("not_facing_camera")

    # ---- head swing to BOTH sides ----
    swing_ok = False
    if len(swing) >= 4:
        dy = [m["yaw"] - yaw0 for m in swing]
        pos, neg = max(dy), min(dy)
        detail.update(swing_pos=round(pos, 1), swing_neg=round(neg, 1))
        swing_ok = pos >= c["swing_deg"] and neg <= -c["swing_deg"]
    if not swing_ok:
        reasons.append("no_head_turn")

    # ---- the randomly assigned extra move ----
    extra_ok = False
    if len(extra_frames) >= 3:
        if extra == "nod":
            pr = [m["pitch"] for m in extra_frames]
            detail["nod_range"] = round(max(pr) - min(pr), 1)
            extra_ok = (max(pr) - min(pr)) >= c["nod_deg"]
        else:  # zoom
            w = [m["width"] for m in extra_frames]
            ratio = max(w) / max(min(w), 1.0)
            detail["zoom_ratio"] = round(ratio, 2)
            extra_ok = ratio >= c["zoom_ratio"]
    if not extra_ok:
        reasons.append("no_nod" if extra == "nod" else "no_zoom")

    # ---- identity: consistent across the session + matches the ID photo ----
    embs = [np.asarray(m["emb"], dtype=np.float32) for m in frames if m.get("ok") and m.get("emb") is not None]
    straight_embs = [np.asarray(m["emb"], dtype=np.float32) for m in straight if m.get("emb") is not None]
    match = None
    consistent = True
    if straight_embs:
        mean = np.mean(np.stack(straight_embs), axis=0)
        mean = mean / (np.linalg.norm(mean) + 1e-9)
        sims = [_cos(mean, e) for e in embs]
        detail["min_consistency"] = round(min(sims), 3) if sims else None
        consistent = (min(sims) >= c["consistency_threshold"]) if sims else False
        if not consistent:
            reasons.append("different_person_in_session")
        if id_embedding is not None:
            match = _cos(mean, np.asarray(id_embedding, dtype=np.float32))
            detail["match_score"] = round(match, 3)
    else:
        consistent = False

    liveness_passed = bool(swing_ok and extra_ok and consistent and len(straight) >= 3
                           and "multiple_faces" not in reasons)
    matched = bool(match is not None and match >= c["match_threshold"])
    if match is not None and not matched:
        reasons.append("face_mismatch")

    verified = liveness_passed and matched
    # keep reasons ordered & unique
    seen, uniq = set(), []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            uniq.append(r)
    return {
        "verified": verified,
        "liveness_passed": liveness_passed,
        "face_matched": matched,
        "match_score": None if match is None else round(float(match), 3),
        "reasons": [] if verified else uniq,
        "detail": detail,
        "thresholds": {"match": c["match_threshold"], "swing_deg": c["swing_deg"]},
    }
