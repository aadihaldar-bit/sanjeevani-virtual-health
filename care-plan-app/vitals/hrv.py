"""Heart rate variability from the pulse signal: beat times -> beat-to-beat intervals -> RMSSD / SDNN. Strictly gated."""
from __future__ import annotations

import numpy as np
from scipy.interpolate import CubicSpline
from scipy.signal import butter, filtfilt, find_peaks

HRV_BAND_HZ = (0.5, 4.0)
UPSAMPLE_HZ = 250.0
MIN_SECONDS = 30.0
MIN_BEATS = 25
TIERS = {"reliable": {"clean": 0.8, "hr_gap": 5.0}, "approximate": {"clean": 0.6, "hr_gap": 8.0}}
TRACK_PENALTY = 4.0


def combine(signals: list[np.ndarray]) -> np.ndarray:
    """Average several models' pulse signals into one: same length, z-scored, signs aligned to the first."""
    n = min(len(s) for s in signals)
    zs = [(s[:n] - s[:n].mean()) / (s[:n].std() or 1.0) for s in signals]
    ref = zs[0]
    return np.mean([z if np.dot(z, ref) >= 0 else -z for z in zs], axis=0)


def beat_times(pulse: np.ndarray, fs: float, hr_bpm: float) -> np.ndarray:
    """Beat times in seconds. Candidate peaks come from a spline-upsampled signal (finer than one frame); then the
    sequence is chosen by dynamic programming: strong peaks score, intervals far from the expected beat period cost.
    This drops the small extra peaks that plain peak picking turns into false beats."""
    t = np.arange(len(pulse)) / fs
    fine_t = np.arange(0, t[-1], 1 / UPSAMPLE_HZ)
    fine = CubicSpline(t, pulse)(fine_t)
    fine = (fine - fine.mean()) / (fine.std() or 1.0)
    period = 60.0 / hr_bpm
    cand, _ = find_peaks(fine, distance=int(0.4 * period * UPSAMPLE_HZ))
    if len(cand) < 2:
        return fine_t[cand]
    ct, amp = fine_t[cand], fine[cand]
    # A long gap (a muted movement stretch) breaks the chain: track each stretch on its own and join them.
    cuts = np.flatnonzero(np.diff(ct) > 1.7 * period) + 1
    out = []
    for seg in np.split(np.arange(len(ct)), cuts):
        if len(seg) >= 2:
            out.extend(_track(ct[seg], amp[seg], period))
    return np.array(out)


def _track(ct: np.ndarray, amp: np.ndarray, period: float) -> list:
    score, prev = amp.copy(), np.full(len(ct), -1)
    for j in range(len(ct)):
        for i in range(j - 1, -1, -1):
            gap = ct[j] - ct[i]
            if gap > 1.7 * period:
                break
            if gap < 0.5 * period:
                continue
            s_ij = score[i] + amp[j] - TRACK_PENALTY * ((gap - period) / period) ** 2
            if s_ij > score[j]:
                score[j], prev[j] = s_ij, i
    path, j = [], int(np.argmax(score))
    while j >= 0:
        path.append(j)
        j = prev[j]
    return list(ct[path[::-1]])


def clean_intervals(ibi: np.ndarray) -> np.ndarray:
    """Keep intervals that look like real beats: near the median, and not jumping >20% from the previous kept one."""
    if len(ibi) == 0:
        return ibi
    med = np.median(ibi)
    ok = (ibi > 0.7 * med) & (ibi < 1.3 * med)
    keep, prev = np.zeros(len(ibi), bool), None
    for i, v in enumerate(ibi):
        if ok[i] and (prev is None or abs(v - prev) <= 0.2 * prev):
            keep[i], prev = True, v
    return keep


def hrv(pulse: np.ndarray, fs: float, hr_bpm: float | None, mask: np.ndarray | None = None) -> dict:
    """pulse: an UNFILTERED pulse signal. The heart-rate band (0.7-3 Hz) smooths away beat-to-beat changes and reads
    RMSSD ~9% low on synthetic tests; 0.5-4 Hz keeps them (~97%) while still removing drift and frame noise."""
    out = {"rmssd_ms": None, "sdnn_ms": None, "reliable": False, "level": None, "warnings": [],
           "note": "Ultra-short HRV from video; 5-minute recordings are the clinical standard."}
    dur = len(pulse) / fs
    if dur > 1.0:
        b, a = butter(3, [HRV_BAND_HZ[0] / (fs / 2), HRV_BAND_HZ[1] / (fs / 2)], btype="band")
        pulse = filtfilt(b, a, pulse - np.mean(pulse))
    if hr_bpm is None:
        out["warnings"].append("no reliable heart rate, so beats cannot be timed")
        return out
    if dur < MIN_SECONDS:
        out["warnings"].append(f"need at least {MIN_SECONDS:.0f}s of pulse signal, have {dur:.0f}s")
        return out
    if mask is not None:
        from .motion import resample_mask
        # Mute set-aside stretches before finding beats: a loud movement burst would otherwise set the signal's scale
        # and make every real beat look small.
        pulse = np.where(resample_mask(mask, len(pulse)), 0.0, pulse)
    beats = beat_times(pulse, fs, hr_bpm)
    ibi = np.diff(beats)
    keep = clean_intervals(ibi)
    still = np.ones(len(ibi), bool)
    if mask is not None and len(ibi):
        from .motion import resample_mask
        m = resample_mask(mask, len(pulse))
        at = np.minimum((beats * fs).astype(int), len(m) - 1)
        c = np.r_[0, np.cumsum(m)]
        still = (c[at[1:] + 1] - c[at[:-1]]) == 0       # an interval overlapping a movement stretch at all is left out
        out["set_aside_intervals"] = int((~still).sum())
    keep = keep & still
    out.update(beats=int(len(beats)), intervals=int(len(ibi)), clean_fraction=round(float(keep.mean()) if len(ibi) else 0.0, 2))
    if len(ibi) < MIN_BEATS:
        out["warnings"].append(f"only {len(ibi)} beat intervals found (need {MIN_BEATS})")
        return out
    good = ibi[keep]
    # RMSSD only over pairs where both intervals were kept, so a dropped beat does not create a fake jump.
    pairs = keep[1:] & keep[:-1]
    diffs = np.diff(ibi)[pairs]
    rmssd = float(np.sqrt(np.mean(diffs ** 2)) * 1000) if len(diffs) else None
    sdnn = float(np.std(good, ddof=1) * 1000) if len(good) > 2 else None
    beat_hr = 60.0 / float(np.mean(good)) if len(good) else None
    out.update(rmssd_ms=round(rmssd, 1) if rmssd else None, sdnn_ms=round(sdnn, 1) if sdnn else None,
               beat_hr_bpm=round(beat_hr, 1) if beat_hr else None)
    clean = float(keep.sum() / max(still.sum(), 1))      # judged on the still part only
    gap = abs(beat_hr - hr_bpm) if beat_hr else float("inf")
    level = next((name for name, g in TIERS.items() if clean >= g["clean"] and gap <= g["hr_gap"]), None)
    if rmssd is None:
        level = None
    out["level"], out["reliable"] = level, level == "reliable"
    if level is None:
        need = TIERS["approximate"]
        if clean < need["clean"]:
            out["warnings"].append(f"only {clean:.1%} of beat intervals look clean (need {need['clean']:.0%})")
        if gap > need["hr_gap"]:
            out["warnings"].append(f"beat-by-beat heart rate ({beat_hr or 0:.0f}) does not match the heart-rate reading ({hr_bpm:.0f})")
        out["rejected"] = {"rmssd_ms": out["rmssd_ms"], "sdnn_ms": out["sdnn_ms"]}
        out["rmssd_ms"] = out["sdnn_ms"] = None          # never report a number the gate rejected
    elif level == "approximate":
        out["warnings"].append(f"approximate: only {clean:.0%} of beat intervals are clean, so treat these as rough figures")
    return out


# ── rough estimate from the phone-video model (train/hrv_step2.py) ─────────────────────────────────────────────────────
# Video beats carry random timing noise s (about 19 ms per beat on UBFC re-encoded like phones). It inflates
# SDNN^2 by 2 s^2 and RMSSD^2 by 6 s^2; both are taken off. Even so the figures are rough: on 28 held-out people
# RMSSD was off by ~17 ms and SDNN by ~12 ms on average, so they are shown with that range and never as "reliable".
SDNN_BANDS = ((30.0, "lower than typical"), (60.0, "typical"), (float("inf"), "higher than typical"))   # resting adults, 1-2 min
ROUGH_MIN_CLEAN = 0.6
FLOOR_MS = 5.0
# A low-confidence figure is pulled toward typical resting values (the UBFC finger-sensor medians), more the fewer clean
# beats there were, and kept inside the usual resting range: noisy beats inflate HRV, so the raw figure would be far off.
TYPICAL_MS = {"rmssd": 28.0, "sdnn": 44.0}
RESTING_RANGE_MS = {"rmssd": (10.0, 100.0), "sdnn": (15.0, 120.0)}


def slope_beats(slope: np.ndarray, fs: float, hr_bpm: float, mask: np.ndarray | None = None):
    """Beat times (s) at the steepest rise of each beat, and per interval whether it is clear of set-aside stretches."""
    b, a = butter(3, [HRV_BAND_HZ[0] / (fs / 2), HRV_BAND_HZ[1] / (fs / 2)], btype="band")
    sig = filtfilt(b, a, slope - np.mean(slope))
    m = None
    if mask is not None:
        from .motion import resample_mask
        m = resample_mask(mask, len(sig))
        sig = np.where(m, 0.0, sig)
    beats = beat_times(sig, fs, hr_bpm)
    still = np.ones(max(len(beats) - 1, 0), bool)
    if m is not None and len(still):
        at = np.minimum((beats * fs).astype(int), len(m) - 1)
        c = np.r_[0, np.cumsum(m)]
        still = (c[at[1:] + 1] - c[at[:-1]]) == 0
    return beats, still


def estimate(slope: np.ndarray, fs: float, hr_bpm: float | None, noise_var_ms2: float, errors_ms: dict,
             mask: np.ndarray | None = None) -> dict:
    """slope: the phone model's output (the pulse wave's slope); its peaks are the steepest rise of each beat."""
    out = {"rmssd_ms": None, "sdnn_ms": None, "level": None, "reliable": False, "band": None, "warnings": [],
           "method": "phone model, steepest-rise beat timing, timing noise removed",
           "note": "Rough estimate from video, not a medical measurement. Typical error on test people: "
                   f"SDNN ±{errors_ms['sdnn']:.0f} ms, RMSSD ±{errors_ms['rmssd']:.0f} ms. 5-minute recordings are the clinical standard."}
    if hr_bpm is None:
        out["warnings"].append("no heart rate, so beats cannot be timed")
        return out
    dur = len(slope) / fs
    beats, still = slope_beats(slope, fs, hr_bpm, mask)
    ibi = np.diff(beats)
    keep = clean_intervals(ibi) & still
    out.update(beats=int(len(beats)), intervals=int(len(ibi)), clean_fraction=round(float(keep.mean()) if len(ibi) else 0.0, 2))
    # Below the quality bar the figure is still given (a demo needs a value), marked low confidence with the reason.
    weak = []
    if dur < MIN_SECONDS:
        weak.append(f"only {dur:.0f}s of pulse signal (want {MIN_SECONDS:.0f}s or more)")
    if len(ibi) < MIN_BEATS:
        weak.append(f"only {len(ibi)} beat intervals (want {MIN_BEATS})")
    if len(ibi) and keep.mean() < ROUGH_MIN_CLEAN:
        weak.append(f"only {keep.mean():.0%} of beat intervals look clean (want {ROUGH_MIN_CLEAN:.0%})")
    clean = float(keep.mean()) if len(ibi) else 0.0
    pairs = keep[1:] & keep[:-1]
    if pairs.sum() < 5:                                 # too few clean pairs: fall back to every plausible interval
        med = np.median(ibi) if len(ibi) else 0
        keep = (ibi > 0.7 * med) & (ibi < 1.3 * med)
        pairs = keep[1:] & keep[:-1]
    if pairs.sum() < 3 or keep.sum() < 3:
        out["warnings"].append(f"only {len(ibi)} beat intervals could be timed; no HRV figure")
        return out
    diffs = np.diff(ibi)[pairs] * 1000
    good = ibi[keep] * 1000
    rmssd_raw, sdnn_raw = float(np.sqrt(np.mean(diffs ** 2))), float(np.std(good, ddof=1))
    rmssd = float(np.sqrt(max(rmssd_raw ** 2 - 6 * noise_var_ms2, FLOOR_MS ** 2)))
    sdnn = float(np.sqrt(max(sdnn_raw ** 2 - 2 * noise_var_ms2, FLOOR_MS ** 2)))
    if weak:
        w = min(1.0, clean / ROUGH_MIN_CLEAN) ** 2
        rmssd, sdnn = (float(np.clip(w * v + (1 - w) * TYPICAL_MS[k], *RESTING_RANGE_MS[k]))
                       for k, v in (("rmssd", rmssd), ("sdnn", sdnn)))
        weak.append(f"beat timing unclear, so the figure leans {1 - w:.0%} toward typical resting values")
    out.update(rmssd_ms=round(rmssd, 1), sdnn_ms=round(sdnn, 1), level="low confidence" if weak else "rough",
               uncorrected={"rmssd_ms": round(rmssd_raw, 1), "sdnn_ms": round(sdnn_raw, 1)},
               range_ms={"sdnn": [round(max(sdnn - errors_ms["sdnn"], 0), 0), round(sdnn + errors_ms["sdnn"], 0)],
                         "rmssd": [round(max(rmssd - errors_ms["rmssd"], 0), 0), round(rmssd + errors_ms["rmssd"], 0)]},
               band=next(name for top, name in SDNN_BANDS if sdnn < top), beat_hr_bpm=round(60000 / float(np.mean(good)), 1))
    out["warnings"].extend(weak)
    if abs(out["beat_hr_bpm"] - hr_bpm) > 8:
        out["warnings"].append(f"beat-by-beat heart rate ({out['beat_hr_bpm']:.0f}) differs from the heart-rate reading "
                               f"({hr_bpm:.0f}); beats may be missed, treat as very rough")
    if dur < 90:
        out["warnings"].append(f"only {dur:.0f}s of signal; 2-5 minutes, sitting still at 60 fps, gives a steadier figure")
    return out
