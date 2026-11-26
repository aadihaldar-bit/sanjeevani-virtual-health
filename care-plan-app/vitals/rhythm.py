"""Did the pulse look steady? "steady", "likely steady" or "uneven in this video", from the beat-to-beat intervals the
phone model times. Only "steady" with clear evidence is normal confidence; the rest are best guesses, marked low.

There is deliberately no "irregular heart" verdict. On UBFC (healthy students at rest, train/rhythm_eval.py) the video's
beat-to-beat change read as irregular (normalised RMSSD over 0.15, an atrial-fibrillation-like level) for 4 of 28
people on uncompressed video and 15 of 28 at phone bitrates, even after the known timing noise was taken off; a
beat-shape quality check did not separate them. Telling healthy people their pulse looks irregular a third of the
time would do harm. A low reading is still informative: noise only adds change, so a steady result means the pulse
really was steady during the clip. AF reads well above it on synthetic beats; no irregular-rhythm video was
available to test on.

A missed beat (an interval about twice the usual) or a false one (two short intervals adding up to one usual) is a
detection slip; too many slips means "can't tell". A premature beat (short interval, then a long one, together about
two usual) is counted on its own and keeps the result from reading "regular".
"""
from __future__ import annotations

import numpy as np

from .hrv import slope_beats

MIN_INTERVALS = 30
MAX_SLIP_SHARE = 0.10
MAX_HR_GAP = 8.0
REGULAR_BELOW = 0.10
MAX_PREMATURE_SHARE = 0.05


def classify(ibi: np.ndarray, still: np.ndarray, noise_var_ms2: float) -> dict:
    """ibi in seconds; still: per interval, clear of set-aside stretches."""
    n = len(ibi)
    med = float(np.median(ibi[still])) if still.any() else float("nan")
    slip, premature = np.zeros(n, bool), np.zeros(n, bool)
    slip |= (ibi > 1.7 * med) & (ibi < 2.3 * med)                                   # a missed beat
    for i in range(n - 1):
        pair = ibi[i] + ibi[i + 1]
        if ibi[i] < 0.75 * med and ibi[i + 1] < 0.75 * med and abs(pair - med) < 0.15 * med:
            slip[i] = slip[i + 1] = True                                            # a false beat in between
        elif ibi[i] < 0.85 * med and ibi[i + 1] > 1.1 * med and abs(pair - 2 * med) < 0.12 * med:
            premature[i] = premature[i + 1] = True
    usable = still & ~slip
    out = {"intervals": int(still.sum()), "slips": int((slip & still).sum()),
           "premature_beats": int((premature & still).sum() // 2)}
    rest = usable & ~premature
    pairs = rest[1:] & rest[:-1]
    d = np.diff(ibi)[pairs] * 1000
    if len(d) < 10:
        out["nrmssd"] = None
        return out
    mean_ms = float(np.mean(ibi[rest])) * 1000
    raw = float(np.sqrt(np.mean(d ** 2)))
    out.update(nrmssd=round(float(np.sqrt(max(raw ** 2 - 6 * noise_var_ms2, 0.0))) / mean_ms, 3),
               nrmssd_uncorrected=round(raw / mean_ms, 3), beat_hr_bpm=round(60000 / mean_ms, 1))
    return out


def regularity(slope: np.ndarray, fs: float, hr_bpm: float | None, noise_var_ms2: float,
               mask: np.ndarray | None = None) -> dict:
    """Always a best reading when beats can be timed: "steady" when the evidence is clear, otherwise "likely steady"
    or "uneven in this video", marked low confidence. Uneven never claims the heart is irregular (see the top)."""
    out = {"level": None, "confidence": None, "warnings": [],
           "note": "Says only whether the pulse looked steady; it cannot detect an irregular heart. If you feel "
                   "palpitations, see a doctor for an ECG."}
    if hr_bpm is None:
        out["warnings"].append("no heart rate, so beats cannot be timed")
        return out
    beats, still = slope_beats(slope, fs, hr_bpm, mask)
    ibi = np.diff(beats)
    if len(ibi) < 10:
        out["warnings"].append(f"only {len(ibi)} beat intervals could be timed")
        return out
    if still.sum() < 10:
        still = np.ones(len(ibi), bool)                 # mostly moving: use every interval, low confidence below
    c = classify(ibi, still, noise_var_ms2)
    out.update(c)
    weak = []
    if still.sum() < MIN_INTERVALS:
        weak.append(f"only {int(still.sum())} beat intervals while still (want {MIN_INTERVALS})")
    slips = c["slips"] / max(c["intervals"], 1)
    if slips > MAX_SLIP_SHARE:
        weak.append(f"beats were missed or doubled in {slips:.0%} of intervals")
    if c["nrmssd"] is None:
        out.update(level="likely steady", confidence="low")
        out["warnings"].append("too few clean beat pairs to judge; shown as the usual case")
        return out
    if abs(c["beat_hr_bpm"] - hr_bpm) > MAX_HR_GAP:
        weak.append(f"beat-by-beat heart rate ({c['beat_hr_bpm']:.0f}) differs from the heart-rate reading ({hr_bpm:.0f})")
    pm = c["premature_beats"] / max(c["intervals"], 1)
    if c["nrmssd"] < REGULAR_BELOW and pm <= MAX_PREMATURE_SHARE:
        out["level"] = "steady"
    elif c["nrmssd"] <= 0.15:
        out["level"], weak = "likely steady", weak + ["beat timing a little uneven, within what video noise causes"]
    else:
        out["level"] = "uneven in this video"
        weak.append("beat timing varied a lot; on healthy people this is usually the video, not the heart")
    out["confidence"] = "low" if weak or out["level"] != "steady" else "normal"
    out["warnings"].extend(weak)
    return out
