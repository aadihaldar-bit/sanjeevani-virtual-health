"""Blood-oxygen INDEX from face video: how strongly the pulse shows in red versus blue/green ("ratio of ratios").
Uncalibrated it is a ratio, not a percentage; a percentage needs a calibration line fitted to oximeter readings."""
from __future__ import annotations

import json
import os

import numpy as np
from scipy.signal import detrend

from .pulse import resample_uniform

WIN_S, STEP_S = 10.0, 5.0
HALF_BAND_HZ = 0.1
MIN_CHANNEL_SNR_DB = 0.0
MIN_WINDOWS = 3
MAX_SPREAD = 0.15                 # interquartile range / median of the per-window ratios
CAL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "calibration", "spo2.json")
CAL_MIN_POINTS, CAL_MIN_RANGE_PCT = 5, 4.0


def _channel_pulse(x: np.ndarray, fs: float, f0: float):
    """Pulse strength of one colour channel relative to its brightness (AC/DC), and how far it stands above noise."""
    dc = float(x.mean())
    y = detrend(x) / max(dc, 1e-6)
    y = y * np.hanning(len(y))
    nfft = 4096
    f = np.fft.rfftfreq(nfft, 1 / fs)
    p = np.abs(np.fft.rfft(y, nfft)) ** 2
    band = (f >= 0.7) & (f <= 3.0)
    at = band & ((np.abs(f - f0) <= HALF_BAND_HZ) | (np.abs(f - 2 * f0) <= HALF_BAND_HZ))
    ac = float(np.sqrt(p[np.abs(f - f0) <= HALF_BAND_HZ].sum()))
    snr = float(10 * np.log10(p[at].sum() / max(p[band & ~at].sum(), 1e-20)))
    return ac, snr


def spo2_index(trace: dict, hr_bpm: float | None, fs: float = 30.0, mask: np.ndarray | None = None) -> dict:
    out = {"ratio_rb": None, "ratio_rg": None, "spo2_pct": None, "reliable": False, "warnings": [],
           "note": "Uncalibrated index. A percentage needs oximeter calibration (vitals calibrate)."}
    if hr_bpm is None:
        out["warnings"].append("no reliable heart rate, so the pulse cannot be located in each colour")
        return out
    t, rgb = trace["t"], trace["rgb"]
    ok = np.isfinite(rgb).all(axis=1)
    if ok.sum() < WIN_S * fs:
        out["warnings"].append(f"need at least {WIN_S:.0f}s of face video")
        return out
    _, even = resample_uniform(t[ok], rgb[ok], fs)
    f0 = hr_bpm / 60.0
    w, s = int(WIN_S * fs), int(STEP_S * fs)
    rows = []
    m = None
    if mask is not None:
        from .motion import resample_mask
        m = resample_mask(mask, len(even))
    for i in range(0, len(even) - w + 1, s):
        if m is not None and m[i:i + w].mean() > 0.25:
            continue
        (ar, sr), (ag, sg), (ab, sb) = (_channel_pulse(even[i:i + w, c], fs, f0) for c in range(3))
        rows.append({"rb": ar / max(ab, 1e-12), "rg": ar / max(ag, 1e-12), "min_snr": min(sr, sg, sb)})
    good = [r for r in rows if r["min_snr"] > MIN_CHANNEL_SNR_DB]
    out["windows"] = f"{len(good)}/{len(rows)}"
    if len(good) < MIN_WINDOWS:
        out["warnings"].append(f"the pulse stands above noise in all three colours in only {len(good)} of {len(rows)} windows "
                               f"(need {MIN_WINDOWS})")
        return out
    rb = np.array([r["rb"] for r in good])
    rg = np.array([r["rg"] for r in good])
    spread = float((np.percentile(rb, 75) - np.percentile(rb, 25)) / np.median(rb))
    out.update(ratio_rb=round(float(np.median(rb)), 3), ratio_rg=round(float(np.median(rg)), 3), spread=round(spread, 3))
    if spread > MAX_SPREAD:
        out["warnings"].append(f"the ratio changes too much between windows ({spread:.0%} spread, need under {MAX_SPREAD:.0%})")
        out["ratio_rb"] = out["ratio_rg"] = None
        return out
    out["reliable"] = True
    cal = load_calibration()
    if cal.get("usable"):
        pct = cal["a"] + cal["b"] * out["ratio_rb"]
        out["spo2_pct"] = round(pct, 1)
        out["note"] = f"Calibrated from {cal['n']} oximeter readings ({cal['min']:.0f}-{cal['max']:.0f}%)."
        if not 85 <= pct <= 100:
            out["warnings"].append("outside the range the calibration was made on; treat as unreliable")
    return out


# ── calibration: pairs of (ratio from a clip, oximeter reading taken during it) ──────────────────────────────────────

def _points(path=CAL_PATH) -> list:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("points", [])
    except FileNotFoundError:
        return []


def add_point(ratio_rb: float, spo2_pct: float, clip: str, path=CAL_PATH) -> int:
    pts = _points(path) + [{"ratio_rb": ratio_rb, "spo2": spo2_pct, "clip": clip}]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"points": pts}, f, indent=2)
    return len(pts)


def fit(points: list) -> dict:
    """SpO2 = a + b * ratio. Refused until the points span enough of the scale for the slope to mean something."""
    n = len(points)
    if n == 0:
        return {"usable": False, "n": 0, "reason": "no calibration points yet"}
    y = np.array([p["spo2"] for p in points], float)
    x = np.array([p["ratio_rb"] for p in points], float)
    res = {"usable": False, "n": n, "min": float(y.min()), "max": float(y.max())}
    if n < CAL_MIN_POINTS:
        return {**res, "reason": f"need {CAL_MIN_POINTS} points, have {n}"}
    if y.max() - y.min() < CAL_MIN_RANGE_PCT:
        return {**res, "reason": f"readings span only {y.max() - y.min():.0f}% (need {CAL_MIN_RANGE_PCT:.0f}%); "
                                 "healthy people sit at 95-99%, so a line through them is mostly noise"}
    b, a = np.polyfit(x, y, 1)
    r = float(np.corrcoef(x, y)[0, 1])
    res.update(a=float(a), b=float(b), r=round(r, 2))
    if b >= 0 or abs(r) < 0.6:
        return {**res, "reason": f"ratio does not track oxygen (slope {b:+.1f}, r {r:+.2f}); expected a clear negative slope"}
    return {**res, "usable": True}


def load_calibration(path=CAL_PATH) -> dict:
    return fit(_points(path))
