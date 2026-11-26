"""Heart-rate estimate and quality verdict from a trace. Thresholds are provisional until checked against real recordings."""
from __future__ import annotations

import numpy as np

from .pulse import bandpass, chrom, heart_rate, pos, resample_uniform

FS = 30.0
MIN_FACE_FRACTION = 0.80
MIN_SECONDS = 15.0
MIN_SNR_DB = 0.0
MAX_METHOD_GAP_BPM = 5.0
MIN_SKIN_BRIGHTNESS = 60.0
MAX_MOTION = 0.03         # std of nose position, as a fraction of frame size


def analyze(trace: dict, fs: float = FS) -> dict:
    t, rgb, nose = trace["t"], trace["rgb"], trace["nose"]
    valid = np.isfinite(rgb).all(axis=1)
    warnings: list[str] = []
    face_fraction = float(valid.mean()) if len(valid) else 0.0
    duration = float(t[-1] - t[0]) if len(t) > 1 else 0.0
    out = {"duration_s": round(duration, 1), "face_fraction": round(face_fraction, 3), "hr_bpm": None, "reliable": False,
           "warnings": warnings, "disclaimer": "Wellness estimates from video, not medical measurements. The model can be wrong, most of all on "
                         "readings marked LOW CONFIDENCE: use them for this session only, never store them as a fixed or "
                         "medical record."}

    if valid.sum() < 2 or duration < MIN_SECONDS:
        warnings.append(f"recording too short ({duration:.0f}s); need at least {MIN_SECONDS:.0f}s")
        return out
    if face_fraction < MIN_FACE_FRACTION:
        warnings.append(f"face found in only {face_fraction:.0%} of frames")

    skin_brightness = float(rgb[valid].mean())
    out["skin_brightness"] = round(skin_brightness, 1)
    if skin_brightness < MIN_SKIN_BRIGHTNESS:
        warnings.append(f"too dark (skin brightness {skin_brightness:.0f}); the pulse is lost in camera noise - add a lamp facing you")
    if trace.get("meta", {}).get("exposure_locked") is False:
        out["notes"] = ["camera exposure could not be locked; its auto-adjustments can disturb the pulse"]

    measured_fps = (valid.sum() - 1) / (t[valid][-1] - t[valid][0])
    out["measured_fps"] = round(float(measured_fps), 1)
    if measured_fps < 20:
        warnings.append(f"camera delivered only {measured_fps:.0f} fps (30 expected)")

    # Gaps without a face are bridged by interpolation; the share of such frames is reported above.
    grid, even = resample_uniform(t[valid], rgb[valid], fs)
    if len(grid) < int(10 * fs):
        warnings.append("not enough usable signal")
        return out

    results = {}
    for name, fn in (("pos", pos), ("chrom", chrom)):
        sig = bandpass(fn(even, fs), fs)
        _, bpm, snr = heart_rate(sig, fs)
        results[name] = {"bpm": float(np.median(bpm)), "snr_db": float(np.median(snr)), "series": bpm}
    out["methods"] = {k: {"bpm": round(v["bpm"], 1), "snr_db": round(v["snr_db"], 1)} for k, v in results.items()}
    out["hr_series_bpm"] = [round(float(x), 1) for x in results["pos"]["series"]]

    gap = abs(results["pos"]["bpm"] - results["chrom"]["bpm"])
    if gap > MAX_METHOD_GAP_BPM:
        warnings.append(f"the two methods disagree by {gap:.0f} bpm")
    if results["pos"]["snr_db"] < MIN_SNR_DB:
        warnings.append("pulse signal is weak (low signal-to-noise)")

    ok_nose = np.isfinite(nose).all(axis=1)
    if ok_nose.sum() > 2:
        motion = float(np.nanstd(nose[ok_nose], axis=0).max())
        out["motion"] = round(motion, 4)
        if motion > MAX_MOTION:
            warnings.append("too much head movement")

    out["reliable"] = (face_fraction >= MIN_FACE_FRACTION and not warnings)
    if out["reliable"]:
        out["hr_bpm"] = round(results["pos"]["bpm"], 1)
    return out
