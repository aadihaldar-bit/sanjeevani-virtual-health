"""The phone-video pulse model: EfficientPhys fine-tuned on UBFC-rPPG re-encoded like phone H.264/H.265 (train/), run on
CPU with onnxruntime. Its pulse ignores the codec keyframe rhythm that misleads the colour methods.

The face is cropped exactly as in training: a Haar face box found every 30 frames on a 640-wide copy, the median box
enlarged 1.5x, each frame's box resized to 72x72 (area averaging), standardised over the whole clip. The video is
read twice (find the box, then crop), so a 1080p clip never has to sit in memory whole.
"""
from __future__ import annotations

import json
import os
import time

import numpy as np
from scipy.signal import detrend

MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
ONNX = os.path.join(MODELS_DIR, "phone_efficientphys.onnx")
META = os.path.join(MODELS_DIR, "phone_efficientphys.json")
CASCADE = os.path.join(MODELS_DIR, "haarcascade_frontalface_default.xml")
NAME = "PhoneEfficientPhys"
DETECT_EVERY = 30
DETECT_WIDTH = 640

_session = None


def available() -> bool:
    return os.path.exists(ONNX) and os.path.exists(META)


def meta() -> dict:
    with open(META, encoding="utf-8") as f:
        return json.load(f)


def _sess():
    global _session
    if _session is None:
        import onnxruntime as ort
        _session = ort.InferenceSession(ONNX, providers=["CPUExecutionProvider"])
    return _session


def _frames(path: str, step: int):
    import av
    with av.open(path) as c:
        for i, f in enumerate(c.decode(video=0)):
            if i % step == 0:
                a = f.to_ndarray(format="rgb24")
                # Phones store portrait video sideways with a rotation flag; turn it upright as a player would.
                yield np.ascontiguousarray(np.rot90(a, f.rotation // 90)) if f.rotation else a


def _fps(path: str) -> float:
    import av
    with av.open(path) as c:
        return float(c.streams.video[0].average_rate or 30)


def face_box(path: str, step: int, box_coef: float) -> np.ndarray | None:
    import cv2
    det = cv2.CascadeClassifier(CASCADE)
    boxes = []
    for i, f in enumerate(_frames(path, step)):
        if i % DETECT_EVERY:
            continue
        s = min(1.0, DETECT_WIDTH / f.shape[1])
        g = cv2.cvtColor(cv2.resize(f, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else f, cv2.COLOR_RGB2GRAY)
        found = det.detectMultiScale(g)
        if len(found):
            boxes.append(found[np.argmax(found[:, 2])] / s)
    if not boxes:
        return None
    x, y, w, h = np.median(np.array(boxes, float), axis=0)
    x, y = max(0.0, x - (box_coef - 1) / 2 * w), max(0.0, y - (box_coef - 1) / 2 * h)
    return np.array([x, y, box_coef * w, box_coef * h]).astype(int)


def crops(path: str, step: int, box: np.ndarray, size: int) -> np.ndarray:
    import cv2
    x, y, w, h = box
    out = []
    for f in _frames(path, step):
        H, W = f.shape[:2]
        out.append(cv2.resize(f[y:min(y + h, H), x:min(x + w, W)].astype(np.float32), (size, size),
                              interpolation=cv2.INTER_AREA))
    return np.stack(out)


def slope(frames: np.ndarray, chunk: int) -> np.ndarray:
    """Model output per frame (the pulse wave's slope), in chunks of `chunk` with the last one overlapping."""
    x = ((frames - frames.mean()) / (frames.std() or 1.0)).transpose(0, 3, 1, 2).astype(np.float32)
    n = len(x)
    starts = list(range(0, n - chunk + 1, chunk))
    if starts[-1] + chunk < n:
        starts.append(n - chunk)
    out = np.zeros(n)
    s = _sess()
    for st in starts:
        c = x[st:st + chunk]
        out[st:st + chunk] = s.run(None, {"frames": np.concatenate([c, c[-1:]])})[0].ravel()
    return out


def run(path: str, log=print) -> dict:
    """Same shape as one entry of deep.run_models (bpm, fs, bvp, raw), plus the slope signal HRV is timed on."""
    from .deep import window_hr
    t0 = time.perf_counter()
    try:
        m = meta()
        fps = _fps(path)
        step = max(1, int(round(fps / m["fps"])))           # the model was trained on 30 fps
        fs = fps / step
        box = face_box(path, step, m["box_coef"])
        if box is None:
            raise ValueError("no face found")
        fr = crops(path, step, box, m["size"])
        if len(fr) < m["chunk"]:
            raise ValueError(f"too short for the model ({len(fr) / fs:.0f}s, need {m['chunk'] / fs:.0f}s)")
        sl = slope(fr, m["chunk"])
        pulse = detrend(np.cumsum(sl))
        hrs, snrs, clean = window_hr(pulse, fs)
        out = {"bpm": float(np.median(hrs)), "spread": [float(hrs.min()), float(hrs.max())],
               "snr_db": float(np.median(snrs)), "fs": fs, "bvp": clean, "raw": pulse, "slope": sl,
               "secs": time.perf_counter() - t0}
    except Exception as e:                      # noqa: BLE001 - the other estimators still run
        out = {"error": f"{type(e).__name__}: {str(e)[:150]}"}
    if log:
        log(f"  {NAME:20s} " + (f"{out['bpm']:5.1f} bpm  ({out['secs']:.0f}s)" if "bpm" in out else "failed: " + out["error"]))
    return out
