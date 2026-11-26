"""Remove the video encoder's keyframe rhythm from per-frame signals.

Phone video (H.264/H.265) stores a full picture every N frames (often every 30 = once a second) and only changes in
between. Skin colour jumps slightly at each full picture, and that once-a-second rhythm reads as a 60 bpm "pulse" to
every method. The keyframe positions are in the file, so the pattern locked to them can be measured and subtracted;
a real heartbeat is not locked to the keyframes and survives. A heart rate that equals the keyframe rhythm cannot be
told apart from it."""
from __future__ import annotations

import numpy as np


def keyframe_index(path: str) -> list[int] | None:
    """Frame numbers (display order) that are keyframes, read from the container without decoding."""
    try:
        import av
        with av.open(path) as c:
            s = c.streams.video[0]
            pk = sorted((p.pts, p.is_keyframe) for p in c.demux(s) if p.size > 0 and p.pts is not None)
        return [i for i, (_, k) in enumerate(pk) if k]
    except Exception:          # noqa: BLE001 - an unreadable container just means no correction
        return None


def gop_length(keys) -> int | None:
    if not keys or len(keys) < 3:
        return None
    d = np.diff(keys)
    return int(np.median(d))


def remove_keyframe_pattern(x: np.ndarray, keys) -> np.ndarray:
    """x: (n,) or (n, ...) per-frame values (NaN allowed). Subtracts the mean shape over the keyframe cycle."""
    gop = gop_length(keys)
    if gop is None or gop < 4:
        return x
    x = np.asarray(x, float)
    n = len(x)
    phase = np.full(n, -1)
    k = np.array([k for k in keys if k < n])
    for a, b in zip(k, list(k[1:]) + [n]):
        phase[a:b] = np.arange(b - a)
    flat = x.reshape(n, -1)
    # Slow changes (drift, breathing) must not leak into the template: compare each frame with its own cycle's mean.
    hp = np.full_like(flat, np.nan)
    for a, b in zip(k, list(k[1:]) + [n]):
        hp[a:b] = flat[a:b] - np.nanmean(flat[a:b], axis=0)
    out = flat.copy()
    for ph in range(gop):
        sel = phase == ph
        if sel.sum() >= 3:
            tmpl = np.nanmean(hp[sel], axis=0)
            out[sel] = flat[sel] - tmpl
    return out.reshape(x.shape)


def clean_trace(tr: dict, keys) -> dict:
    """Same trace with the keyframe pattern taken out of the skin colours (whole face and each patch)."""
    if not keys or gop_length(keys) is None:
        return tr
    tr = dict(tr)
    tr["rgb"] = remove_keyframe_pattern(tr["rgb"], keys)
    if tr.get("patches") is not None:
        tr["patches"] = remove_keyframe_pattern(tr["patches"], keys)
    return tr
