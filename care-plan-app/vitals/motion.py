"""Find stretches where the head moves or the mouth works (talking), so they can be left out rather than averaged in.
Movement and talking are the main sources of large rPPG errors; a still stretch measured alone beats a whole clip."""
from __future__ import annotations

import numpy as np

HEAD_SPEED_MIN = 0.03         # frame-widths per second; a still seated person drifts far less
MOUTH_SPEED_MIN = 0.25        # lip gap (fraction of face height) per second
RELATIVE = 5.0                # ...or this many times the clip's own typical speed, whichever is larger
PAD_S = 0.5
SMOOTH_S = 0.2
WINDOW_MAX_MASKED = 0.25


def _speed(x: np.ndarray, t: np.ndarray, smooth_n: int) -> np.ndarray:
    x = np.asarray(x, float)
    if x.ndim == 1:
        x = x[:, None]
    ok = np.isfinite(x).all(axis=1)
    if ok.sum() < 3:
        return np.zeros(len(t))
    filled = np.column_stack([np.interp(t, t[ok], x[ok, c]) for c in range(x.shape[1])])
    v = np.linalg.norm(np.gradient(filled, t, axis=0), axis=1)
    if smooth_n > 1:
        v = np.convolve(v, np.ones(smooth_n) / smooth_n, mode="same")
    return v


def motion_mask(trace: dict) -> dict:
    """Per-frame True where the frame should be set aside, plus a summary of why."""
    t = np.asarray(trace["t"], float)
    n = len(t)
    out = {"mask": np.zeros(n, bool), "fraction": 0.0, "bursts": 0, "reasons": {}}
    if n < 3:
        return out
    fs = (n - 1) / max(t[-1] - t[0], 1e-6)
    sm = max(1, int(SMOOTH_S * fs))
    flags = {}
    head = _speed(trace["nose"], t, sm)
    flags["head movement"] = head > max(HEAD_SPEED_MIN, RELATIVE * float(np.median(head)))
    if trace.get("mouth") is not None:
        mouth = _speed(trace["mouth"], t, sm)
        flags["talking or mouth movement"] = mouth > max(MOUTH_SPEED_MIN, RELATIVE * float(np.median(mouth)))
    flags["face lost"] = ~np.isfinite(np.asarray(trace["rgb"], float)).all(axis=1)
    raw = np.zeros(n, bool)
    for f in flags.values():
        raw |= f
    pad = int(PAD_S * fs)
    mask = raw.copy()
    for i in np.flatnonzero(raw):
        mask[max(0, i - pad):i + pad + 1] = True
    starts = np.flatnonzero(mask & ~np.r_[False, mask[:-1]])
    out.update(mask=mask, fraction=round(float(mask.mean()), 3), bursts=int(len(starts)),
               reasons={k: round(float(v.mean()), 3) for k, v in flags.items() if v.any()})
    return out


def resample_mask(mask: np.ndarray, n_out: int) -> np.ndarray:
    """Stretch a per-frame mask onto a signal of n_out samples covering the same time."""
    if len(mask) == 0 or n_out == 0:
        return np.zeros(n_out, bool)
    idx = np.minimum((np.arange(n_out) * len(mask) / n_out).astype(int), len(mask) - 1)
    return mask[idx]


def window_factors(mask: np.ndarray, fs: float, win_s: float, step_s: float, n_windows: int) -> np.ndarray:
    """Weight multiplier per analysis window: 1 when still, 0 when more than a quarter of it is set aside."""
    w, s = int(win_s * fs), int(step_s * fs)
    f = np.ones(n_windows)
    for j in range(n_windows):
        seg = mask[j * s:j * s + w]
        if len(seg) and seg.mean() > WINDOW_MAX_MASKED:
            f[j] = 0.0
    return f


def bridge(x: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Replace set-aside samples by a straight line between the still samples either side (for slow signals)."""
    x = np.asarray(x, float).copy()
    m = resample_mask(mask, len(x)) | ~np.isfinite(x)
    if m.all() or not m.any():
        return x
    idx = np.arange(len(x))
    x[m] = np.interp(idx[m], idx[~m], x[~m])
    return x
