"""Blinks and eye closure from the face landmarks: blink rate, the longest closure, and PERCLOS (the share of time the
eyes are mostly closed, the standard drowsiness measure). Openness is judged against the person's own open-eye level
in this clip, so eye shape and camera distance do not matter. Not a medical or fitness-to-drive test."""
from __future__ import annotations

import numpy as np

MIN_SECONDS = 20.0                # below these the reading is still given, marked low confidence
MIN_FACE = 0.8
MIN_FPS = 15.0                    # a blink lasts 0.1-0.4 s; fewer frames than this miss most of them
CLOSED = 0.5                      # openness under half the open level counts as closed (a blink)
MOSTLY_CLOSED = 0.3               # about 80% closed, for PERCLOS
MAX_BLINK_S = 0.5                 # a closure longer than this is not a blink
LONG_CLOSURE_S = 1.0
PERCLOS_DROWSY = 0.15             # Wierwille & Ellsworth: sustained PERCLOS over ~15% goes with drowsy driving
RATE_BANDS = ((8.0, "low (common while concentrating on a screen)"), (21.0, "typical"),
              (float("inf"), "high (can come with tiredness, dry eyes or stress)"))
MIN_OPEN_LEVEL = 0.12             # below this the eyes are never clearly seen open (glasses glare, looking down, far away)


def _runs(flag: np.ndarray):
    """(start, length) of each stretch where flag is True."""
    d = np.diff(np.r_[0, flag.astype(int), 0])
    starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
    return list(zip(starts, ends - starts))


def eyes(trace: dict) -> dict:
    out = {"blinks_per_min": None, "rate_band": None, "perclos": None, "longest_closure_s": None, "long_closures": None,
           "state": None, "warnings": [],
           "note": "Rough indicator from video, not a medical or fitness-to-drive test; looking down or talking can look like closing the eyes."}
    ear = trace.get("eye")
    if ear is None:
        out["warnings"].append("this trace was recorded before eye tracking was added")
        return out
    t, ear = np.asarray(trace["t"], float), np.asarray(ear, float)
    dur = float(t[-1] - t[0]) if len(t) > 1 else 0.0
    seen = np.isfinite(ear)
    if dur < 3 or seen.sum() < 30:
        out["warnings"].append("the face was seen too briefly to watch the eyes")
        return out
    weak = []
    if dur < MIN_SECONDS:
        weak.append(f"only {dur:.0f}s of video (want {MIN_SECONDS:.0f}s)")
    if seen.mean() < MIN_FACE:
        weak.append(f"face seen in only {seen.mean():.0%} of frames (want {MIN_FACE:.0%})")
    fs = (len(t) - 1) / dur
    if fs < MIN_FPS:
        weak.append(f"only {fs:.0f} frames per second, so short blinks are missed (want {MIN_FPS:.0f})")
    open_level = float(np.percentile(ear[seen], 90))
    if open_level < MIN_OPEN_LEVEL:
        weak.append("the eyes are never clearly seen open (glasses glare, looking down, or too far from the camera)")
    o = np.where(seen, ear / open_level, np.nan)
    closed = np.nan_to_num(o, nan=1.0) < CLOSED          # a frame without a face is not a closed eye
    # Landmark jitter can split one blink: join closures one open frame apart. A single closed frame at 25+ fps is jitter.
    for s, n in _runs(~closed):
        if n == 1 and 0 < s < len(closed) - 1:
            closed[s] = True
    min_frames = 2 if fs >= 25 else 1
    durs = [n / fs for _, n in _runs(closed) if n >= min_frames]
    blinks = [d for d in durs if d <= MAX_BLINK_S]
    long_ = [d for d in durs if d > MAX_BLINK_S]
    minutes = seen.sum() / fs / 60
    rate = len(blinks) / minutes
    perclos = float(np.mean(o[seen] < MOSTLY_CLOSED))
    out.update(blinks=len(blinks), blinks_per_min=round(rate, 1), rate_band=next(n for top, n in RATE_BANDS if rate < top),
               perclos=round(perclos, 3), longest_closure_s=round(max(durs), 2) if durs else 0.0,
               long_closures=sum(d >= LONG_CLOSURE_S for d in long_), open_level=round(open_level, 3))
    drowsy = []
    if perclos > PERCLOS_DROWSY:
        drowsy.append(f"eyes mostly closed {perclos:.0%} of the time")
    if out["long_closures"]:
        drowsy.append(f"{out['long_closures']} closure(s) of a second or more")
    out["state"] = "signs of drowsiness" if drowsy else "alert"
    out["reasons"] = drowsy or [f"eyes mostly closed {perclos:.0%} of the time, no long closures"]
    if dur < 60:
        out["warnings"].append(f"only {dur:.0f}s; blink rate settles over a minute or more")
    out["confidence"] = "low" if weak else "normal"
    out["warnings"].extend(weak)
    return out
