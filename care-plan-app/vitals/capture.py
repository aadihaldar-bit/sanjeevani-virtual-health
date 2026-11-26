"""Capture spec for video sent to the vitals analysis, and a checker that says what to fix in a given clip.
The figures follow the rPPG literature (compression is the main enemy: about 10 Mb/s or more, H.265 preferred) and
what was seen on real clips here (a keyframe every second leaves a 60 bpm artifact; a bright window behind the person
makes the camera keep re-exposing)."""
from __future__ import annotations

import cv2
import numpy as np

SPEC = {
    "duration_s": (45, 90),            # >= 30 s is the floor for breathing/HRV; 60 s is the target
    "fps_min": 29.0,                   # 60 fps preferred when the phone offers it
    "bitrate_mbps_min": 8.0,
    "codecs_preferred": ("hevc", "h265"),
    "gop_frames_min": 30,
    "face_width_frac": (0.30, 0.75),   # face fills a good part of the frame but head and shoulders stay in view
    "face_brightness": (90, 210),
    "backlight_ratio_max": 1.4,        # background brighter than the face by more than this = backlit
}

SPEC_TEXT = """Recording a clip for vitals
  - 45-90 seconds, phone on a stand (not hand-held), front camera at eye level, about an arm's length away
  - head AND shoulders in frame; face roughly a third to two-thirds of the frame width
  - light IN FRONT of the person (face a window or lamp); no bright window or lamp behind them
  - sit still and stay quiet: no talking, no nodding; breathe normally
  - camera settings: 30 fps or 60 fps, highest quality / H.265 (HEVC), no beauty filter, no stabilisation crop
  - send the original file (not through WhatsApp/Telegram, which re-compress it)
  - right after recording, count the pulse at the wrist for 30 seconds and double it (a free reference reading)"""


def check(path: str, sample_frames: int = 12) -> dict:
    """Reads the container and a few frames; returns {'ok': bool, 'issues': [...], 'facts': {...}}."""
    issues, facts = [], {}
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return {"ok": False, "issues": ["cannot open the file"], "facts": {}}
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    dur = n / fps if fps else 0.0
    facts.update(fps=round(fps, 1), duration_s=round(dur, 1), width=int(cap.get(3)), height=int(cap.get(4)))
    try:
        import av
        with av.open(path) as c:
            st = c.streams.video[0]
            facts["codec"] = st.codec_context.name
            br = st.bit_rate or c.bit_rate
            facts["bitrate_mbps"] = round(br / 1e6, 1) if br else None
    except Exception:  # noqa: BLE001
        facts["codec"] = facts["bitrate_mbps"] = None
    from .codec import gop_length, keyframe_index
    facts["keyframe_every"] = gop_length(keyframe_index(path))

    lo, hi = SPEC["duration_s"]
    if dur < lo:
        issues.append(f"too short ({dur:.0f}s); record {lo}-{hi}s")
    if fps < SPEC["fps_min"]:
        issues.append(f"frame rate {fps:.0f} fps; use 30 or 60 fps")
    if facts.get("bitrate_mbps") is not None and facts["bitrate_mbps"] < SPEC["bitrate_mbps_min"]:
        issues.append(f"heavily compressed ({facts['bitrate_mbps']} Mb/s); use the highest quality setting (8+ Mb/s)")
    if facts.get("codec") and facts["codec"] not in SPEC["codecs_preferred"]:
        issues.append(f"codec {facts['codec']}: H.265/HEVC keeps the pulse better if the phone offers it")
    if facts["keyframe_every"] is not None and facts["keyframe_every"] <= SPEC["gop_frames_min"] and facts.get("codec") not in SPEC["codecs_preferred"]:
        issues.append(f"a full picture every {facts['keyframe_every']} frames with {facts.get('codec')} leaves a once-a-second artifact "
                      "(corrected in analysis, but a heart rate near that rhythm cannot be told apart)")

    from .face import FaceSampler
    sampler = FaceSampler()
    sizes, face_b, back_b, found = [], [], [], 0
    for i in np.linspace(0, max(n - 1, 0), sample_frames).astype(int):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok, f = cap.read()
        if not ok:
            continue
        s = sampler.sample(f)
        if s is None:
            continue
        found += 1
        sizes.append(s["face_size"])
        face_b.append(s["face_brightness"])
        x0, y0, x1, y1 = (int(v) for v in s["bbox"])
        top = f[:max(1, y0 // 2)]                       # the area above the head: usually wall or window
        back_b.append(float(top.mean()))
    cap.release()
    facts["face_found"] = f"{found}/{sample_frames}"
    if found < sample_frames * 0.8:
        issues.append(f"face found in only {found} of {sample_frames} sampled frames; face the camera")
    if sizes:
        fw = float(np.median(sizes))
        facts["face_width_frac"] = round(fw, 2)
        if fw < SPEC["face_width_frac"][0]:
            issues.append(f"face is small in the frame ({fw:.0%} of the width); move closer")
        elif fw > SPEC["face_width_frac"][1]:
            issues.append(f"face fills {fw:.0%} of the width; move back so head and shoulders are in view")
    if face_b:
        fb, bb = float(np.median(face_b)), float(np.median(back_b))
        facts.update(face_brightness=round(fb), background_brightness=round(bb))
        if fb < SPEC["face_brightness"][0]:
            issues.append(f"face is dim (brightness {fb:.0f}); add light in front of the person")
        elif fb > SPEC["face_brightness"][1]:
            issues.append(f"face is very bright ({fb:.0f}); avoid direct glare")
        if bb > SPEC["backlight_ratio_max"] * fb:
            issues.append("backlit: the background is much brighter than the face; turn so the window or lamp is in front")
    return {"ok": not issues, "issues": issues, "facts": facts}
