"""Pretrained rPPG models (open-rppg, CPU) and the agreement vote that turns many estimates into one reading."""
from __future__ import annotations

import os
import time

import numpy as np
from scipy.signal import butter, detrend, filtfilt

from .pulse import spectrum_peak

MODELS = ["TSCAN.rlap", "TSCAN.pure", "EfficientPhys.rlap", "PhysNet.rlap", "FacePhys.rlap", "PhysFormer.rlap", "RhythmMamba.rlap"]
WIN_S, STEP_S = 8.0, 2.0
AGREE_BPM, MIN_AGREE, PLAUSIBLE = 5.0, 4, (40.0, 180.0)


def _rppg():
    os.environ.setdefault("KERAS_BACKEND", "jax")
    os.environ.setdefault("JAX_PLATFORMS", "cpu")
    import rppg
    return rppg


def _pulse(m) -> np.ndarray:
    sig, _ = m.collect_signals()
    b = np.asarray(sig.get("bvp", []), float)
    bad = ~np.isfinite(b)
    if bad.any():
        b = b[:int(np.argmax(bad))]          # the library leaves NaN on the last partial chunk
    if m.meta.get("cumsum_output"):
        b = np.cumsum(b)
    return b


def window_hr(bvp: np.ndarray, fs: float, win_s: float = WIN_S, step_s: float = STEP_S):
    """8 s windows, Google PHRM-style: returns (hr per window, snr per window)."""
    b, a = butter(3, [0.7 / (fs / 2), 3.0 / (fs / 2)], btype="band")
    x = filtfilt(b, a, detrend(bvp))
    w, s = int(win_s * fs), int(step_s * fs)
    out = [spectrum_peak(x[i:i + w], fs) for i in range(0, len(x) - w + 1, s)]
    return np.array([p * 60 for p, _ in out]), np.array([q for _, q in out]), x


def run_models(video_path: str, models: list[str] | None = None, log=print) -> dict:
    """Each model's pulse signal and window-median HR. A model that fails is reported, not fatal."""
    rppg = _rppg()
    out = {}
    for name in models or MODELS:
        t0 = time.perf_counter()
        try:
            m = rppg.Model(name)
            m.process_video(video_path)
            bvp = _pulse(m)
            if len(bvp) < WIN_S * m.fps:
                raise ValueError("too little signal")
            hrs, snrs, clean = window_hr(bvp, m.fps)
            out[name] = {"bpm": float(np.median(hrs)), "spread": [float(hrs.min()), float(hrs.max())],
                         "snr_db": float(np.median(snrs)), "fs": m.fps, "bvp": clean, "raw": detrend(bvp),
                         "secs": time.perf_counter() - t0}
        except Exception as e:                      # noqa: BLE001 - one bad model must not sink the others
            out[name] = {"error": f"{type(e).__name__}: {str(e)[:150]}"}
        if log:
            r = out[name]
            log(f"  {name:20s} " + (f"{r['bpm']:5.1f} bpm  ({r['secs']:.0f}s)" if "bpm" in r else "failed: " + r["error"]))
    return out


def consensus(estimates: dict, agree_bpm: float = AGREE_BPM, min_agree: int = MIN_AGREE, plausible=PLAUSIBLE) -> dict:
    """Largest group of estimates within ±agree_bpm of a common centre; a reading only when min_agree of them agree."""
    items = sorted((v, k) for k, v in estimates.items() if v is not None and plausible[0] <= v <= plausible[1])
    def largest(pool):
        best = []
        for i in range(len(pool)):
            group = [x for x in pool[i:] if x[0] - pool[i][0] <= 2 * agree_bpm]
            tighter = best and len(group) == len(best) and np.ptp([v for v, _ in group]) < np.ptp([v for v, _ in best])
            if len(group) > len(best) or tighter:
                best = group
        return best

    best = largest(items)
    rival = largest([x for x in items if x not in best])
    centre = float(np.median([v for v, _ in best])) if best else None
    # A second, separate group as large as the first means the clip supports two answers: say so instead of picking one.
    ambiguous = bool(best) and len(rival) >= len(best)
    return {"hr": round(centre, 1) if centre is not None and len(best) >= min_agree and not ambiguous else None,
            "candidate": round(centre, 1) if centre is not None else None,
            "agree": [k for _, k in best], "n_agree": len(best), "n_estimates": len(estimates),
            "ambiguous": ambiguous,
            "rival": {"candidate": round(float(np.median([v for v, _ in rival])), 1), "agree": [k for _, k in rival]} if ambiguous else None}
