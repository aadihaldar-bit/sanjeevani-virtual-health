"""Pulse strength: how much the skin's green brightness swells with each beat, as a percent of its brightness (AC/DC,
the camera's version of an oximeter's perfusion index). Light, camera, skin tone, make-up and video compression all
change it, so it means something only against the same person's earlier clips taken the same way."""
from __future__ import annotations

import numpy as np
from scipy.signal import butter, sosfiltfilt

from .pulse import resample_uniform
from .spo2 import _channel_pulse

WIN_S, STEP_S = 10.0, 5.0
HALF_BAND_HZ = 0.15
MIN_SNR_DB = 0.0
MIN_WINDOWS = 3


def pulse_strength(trace: dict, hr_bpm: float | None, fs: float = 30.0, mask: np.ndarray | None = None) -> dict:
    out = {"pct": None, "warnings": [],
           "note": "Relative only: compare with your own earlier clips from the same phone, light and distance, never "
                   "with other people."}
    if hr_bpm is None:
        out["warnings"].append("no heart rate, so the pulse cannot be picked out")
        return out
    t, rgb = trace["t"], trace["rgb"]
    ok = np.isfinite(rgb).all(axis=1)
    if ok.sum() < WIN_S * fs:
        out["warnings"].append(f"need at least {WIN_S:.0f}s of face video")
        return out
    _, even = resample_uniform(t[ok], rgb[ok], fs)
    g = even[:, 1]
    f0 = hr_bpm / 60.0
    sos = butter(2, [max(f0 - HALF_BAND_HZ, 0.5), f0 + HALF_BAND_HZ], btype="band", fs=fs, output="sos")
    m = None
    if mask is not None:
        from .motion import resample_mask
        m = resample_mask(mask, len(g))
    w, s = int(WIN_S * fs), int(STEP_S * fs)
    vals, n = [], 0
    for i in range(0, len(g) - w + 1, s):
        if m is not None and m[i:i + w].mean() > 0.25:
            continue
        n += 1
        seg = g[i:i + w]
        if _channel_pulse(seg, fs, f0)[1] <= MIN_SNR_DB:
            continue
        y = sosfiltfilt(sos, seg / max(seg.mean(), 1e-6) - 1)
        vals.append(np.sqrt(2) * np.std(y[w // 10:-w // 10]) * 100)            # sine amplitude; filter edges dropped
    out["windows"] = f"{len(vals)}/{n}"
    out["confidence"] = "normal"
    if len(vals) < MIN_WINDOWS:
        # Best guess: the amplitude at the heart rate in every window, noise included, so it likely reads high.
        out["confidence"] = "low"
        out["warnings"].append(f"the pulse stands above noise in the green colour in only {len(vals)} of {n} windows "
                               f"(want {MIN_WINDOWS}); this figure includes noise and likely reads high")
        vals = []
        for i in range(0, len(g) - w + 1, s):
            seg = g[i:i + w]
            y = sosfiltfilt(sos, seg / max(seg.mean(), 1e-6) - 1)
            vals.append(np.sqrt(2) * np.std(y[w // 10:-w // 10]) * 100)
        if not vals:
            return out
    v = np.array(vals)
    out.update(pct=round(float(np.median(v)), 3), spread=round(float((np.percentile(v, 75) - np.percentile(v, 25)) / np.median(v)), 2))
    return out
