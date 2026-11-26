"""Breathing rate from video: chest and head movement, plus the way breathing modulates the pulse. Experimental."""
from __future__ import annotations

import cv2
import numpy as np
from scipy.signal import butter, detrend, find_peaks, hilbert, sosfiltfilt

RR_LO_HZ, RR_HI_HZ = 0.1, 0.6          # 6-36 breaths per minute
RR_PLAUSIBLE = (6.0, 40.0)
MIN_SECONDS = 20.0
MIN_VOTE_SNR_DB = 0.0


class ChestTracker:
    """Vertical shift of the area under the chin against the first frame (phase correlation, sub-pixel)."""

    def __init__(self, max_width: int = 320):
        self.max_width, self.roi, self.ref, self.win, self.scale = max_width, None, None, None, 1.0

    def _prep(self, frame):
        x0, y0, x1, y1 = self.roi
        g = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
        if self.scale < 1:
            g = cv2.resize(g, None, fx=self.scale, fy=self.scale, interpolation=cv2.INTER_AREA)
        return g.astype(np.float32)

    def start(self, frame, bbox) -> bool:
        h_img, w_img = frame.shape[:2]
        x0, y0, x1, y1 = bbox
        w, h = x1 - x0, y1 - y0
        roi = (max(0, int(x0 - 0.3 * w)), min(h_img, int(y1 + 0.1 * h)), min(w_img, int(x1 + 0.3 * w)), min(h_img, int(y1 + 1.0 * h)))
        if roi[3] - roi[1] < 24 or roi[2] - roi[0] < 48:
            self.roi = "none"
            return False
        self.roi = roi
        self.scale = min(1.0, self.max_width / (roi[2] - roi[0]))
        self.ref = self._prep(frame)
        self.win = cv2.createHanningWindow(self.ref.shape[::-1], cv2.CV_32F)
        return True

    def update(self, frame, s) -> float:
        if self.roi is None:
            if s is None or not self.start(frame, s["bbox"]):
                return np.nan
            return 0.0
        if self.roi == "none":
            return np.nan
        # OpenCV 5's phaseCorrelate overwrites its input arrays, so the stored reference must not be passed directly.
        (_, dy), response = cv2.phaseCorrelate(self.ref.copy(), self._prep(frame), self.win)
        return dy / self.scale if response > 0.05 else np.nan

    @property
    def visible(self) -> bool:
        return isinstance(self.roi, tuple)


def _fill(x: np.ndarray) -> np.ndarray | None:
    ok = np.isfinite(x)
    if ok.mean() < 0.7:
        return None
    idx = np.arange(len(x))
    return np.interp(idx, idx[ok], x[ok])


def rr_spectrum(x: np.ndarray, fs: float):
    """Dominant breathing frequency (breaths/min) and SNR in dB."""
    sos = butter(2, [RR_LO_HZ, RR_HI_HZ], btype="band", fs=fs, output="sos")
    y = sosfiltfilt(sos, detrend(x))
    y = (y - y.mean()) * np.hanning(len(y))
    nfft = 1 << 16
    f = np.fft.rfftfreq(nfft, 1 / fs)
    p = np.abs(np.fft.rfft(y, nfft)) ** 2
    band = (f >= RR_LO_HZ) & (f <= RR_HI_HZ)
    fb, pb = f[band], p[band]
    k = int(np.argmax(pb))
    near = (np.abs(fb - fb[k]) <= 0.04) | (np.abs(fb - 2 * fb[k]) <= 0.04)
    return float(fb[k] * 60), float(10 * np.log10(pb[near].sum() / max(pb[~near].sum(), 1e-12)))


def motion_signals(trace: dict, fs: float = 30.0) -> dict:
    """Head and chest vertical movement, resampled to a fixed rate."""
    t = trace["t"]
    out = {}
    if len(t) < 2 or t[-1] - t[0] < MIN_SECONDS:
        return out
    grid = np.arange(t[0], t[-1], 1 / fs)
    head = trace.get("face_y")
    if head is None and "nose" in trace:
        head = trace["nose"][:, 1]
    for name, x in (("head", head), ("chest", trace.get("chest_dy"))):
        if x is None:
            continue
        x = _fill(np.asarray(x, float))
        if x is not None:
            out[name] = np.interp(grid, t, x)
    return out


def pulse_derived(raw: np.ndarray, clean: np.ndarray) -> dict:
    """Breathing shifts the pulse's baseline and changes its strength; both carry the breathing rhythm."""
    return {"pulse_baseline": raw, "pulse_strength": np.abs(hilbert(clean))}


def analyze_breathing(trace: dict, models: dict | None = None, use_models: list[str] | None = None, fs: float = 30.0,
                      mask: np.ndarray | None = None) -> dict:
    """Each source is one vote: head movement, chest movement, and the two pulse-derived signals (median over the
    pretrained models that agreed on heart rate). A reading needs two sources within ±2 breaths/min."""
    from .deep import consensus
    sigs = motion_signals(trace, fs)
    if mask is not None:
        from .motion import bridge
        sigs = {k: bridge(x, mask) for k, x in sigs.items()}   # a nod or a word is not a breath
    methods = estimate(sigs, fs)
    arrays = {k: (x, fs) for k, x in sigs.items()}         # each source's signal, for the breathing pattern
    if models:
        names = [n for n in (use_models or models) if "raw" in models.get(n, {})]
        per_type: dict = {}
        for n in names:
            m = models[n]
            for kind, x in pulse_derived(m["raw"], m["bvp"]).items():
                r = estimate({kind: x}, m["fs"]).get(kind)
                if r:
                    per_type.setdefault(kind, []).append(r)
                    if r["snr_db"] >= max(q["snr_db"] for q in per_type[kind]):
                        arrays[kind] = (x, m["fs"])
        for kind, rs in per_type.items():
            methods[kind] = {"brpm": round(float(np.median([r["brpm"] for r in rs])), 1),
                             "snr_db": round(float(np.median([r["snr_db"] for r in rs])), 1), "models": len(rs)}
    # Only sources whose breathing rhythm stands above their own noise vote; the rest are shown but not counted.
    voters = {k: v["brpm"] for k, v in methods.items() if v["snr_db"] > MIN_VOTE_SNR_DB}
    c = consensus(voters, agree_bpm=2.0, min_agree=2, plausible=RR_PLAUSIBLE)
    c["n_estimates"] = len(methods)
    warnings = []
    if not trace.get("meta", {}).get("chest_visible", False):
        warnings.append("chest not in view; frame head and shoulders for a better breathing reading")
    if not methods:
        warnings.append(f"need at least {MIN_SECONDS:.0f}s of video for breathing")
    rr, confidence = c["hr"], "normal"
    if rr is None and methods:
        # No two sources agree: best guess is the largest group's centre, else the clearest single source.
        confidence = "low"
        if c["candidate"] is not None:
            rr = c["candidate"]
        else:
            top = max(methods, key=lambda k: methods[k]["snr_db"])
            rr, c["agree"] = methods[top]["brpm"], [top]
        warnings.append("the breathing sources do not agree; this is the best guess")
    pat = None
    if rr is not None:
        best = max((k for k in c["agree"] if k in arrays), key=lambda k: methods[k]["snr_db"], default=None)
        pat = pattern(*arrays[best], rr, mask) if best else None
        if pat:
            pat["from"] = best
    return {"methods": methods, "consensus": c, "rr_brpm": rr, "confidence": confidence if rr is not None else None,
            "warnings": warnings, "pattern": pat,
            "note": "Experimental: check against a counted or paced breathing rate."}


# ── breathing pattern ─────────────────────────────────────────────────────────────────────────────────────────────────
# Breath-to-breath intervals from the clearest source that agreed on the rate. Resting breathing varies by about
# 15-30% from breath to breath (coefficient of variation); far more is irregular. A pause is a gap of 10 s or more
# (the usual apnoea definition) with no breath in it; a sigh is a breath more than 2.5x the usual depth.
PATTERN_MIN_SECONDS = 60.0         # below these the pattern is still given, marked low confidence
PATTERN_MIN_BREATHS = 8
STEADY_CV, IRREGULAR_CV = 0.25, 0.45
PAUSE_S = 10.0
SIGH_DEPTH = 2.5


def pattern(x: np.ndarray, fs: float, rr_brpm: float, mask: np.ndarray | None = None) -> dict:
    out = {"level": None, "warnings": [],
           "note": "Rough pattern from video movement and pulse; talking, nodding or shifting can look like irregular breathing."}
    dur = len(x) / fs
    weak = []
    if dur < PATTERN_MIN_SECONDS:
        weak.append(f"only {dur:.0f}s of video (want {PATTERN_MIN_SECONDS:.0f}s for the pattern)")
    sos = butter(2, [RR_LO_HZ * 0.8, RR_HI_HZ], btype="band", fs=fs, output="sos")
    y = sosfiltfilt(sos, detrend(np.asarray(x, float)))
    m = None
    if mask is not None:
        from .motion import resample_mask
        m = resample_mask(mask, len(y))
    period = 60.0 / rr_brpm
    depth = np.std(y if m is None else y[~m]) or 1.0
    peaks, prop = find_peaks(y, distance=max(1, int(0.5 * period * fs)), prominence=0.3 * depth)
    if m is not None:
        keep = ~m[peaks]
        peaks, prom = peaks[keep], prop["prominences"][keep]
    else:
        prom = prop["prominences"]
    if len(peaks) < 3:
        out["warnings"].append(f"only {len(peaks)} clear breaths found")
        return out
    if len(peaks) < PATTERN_MIN_BREATHS:
        weak.append(f"only {len(peaks)} clear breaths (want {PATTERN_MIN_BREATHS})")
    tb = peaks / fs
    ii = np.diff(tb)
    if m is not None:                                          # a gap that spans a set-aside stretch says nothing
        c = np.r_[0, np.cumsum(m)]
        ok = (c[peaks[1:]] - c[peaks[:-1]]) == 0
    else:
        ok = np.ones(len(ii), bool)
    if ok.sum() < 2:
        ok = np.ones(len(ii), bool)
        weak.append("most of the clip had movement; judged on all of it")
    elif ok.sum() < PATTERN_MIN_BREATHS - 1:
        weak.append("much of the clip was set aside for movement")
    cv = float(np.std(ii[ok]) / np.mean(ii[ok]))
    pauses = [round(float(g), 1) for g in ii[ok] if g >= max(PAUSE_S, 2.5 * period)]
    sighs = int((prom > SIGH_DEPTH * np.median(prom)).sum())
    level = "steady" if cv < STEADY_CV else ("somewhat variable" if cv < IRREGULAR_CV else "irregular")
    out.update(level=level, variability=round(cv, 2), breaths=int(len(peaks)), pauses_s=pauses, sighs=sighs,
               confidence="low" if weak else "normal")
    out["warnings"].extend(weak)
    if pauses:
        out["warnings"].append(f"{len(pauses)} pause(s) of {', '.join(f'{p:.0f}' for p in pauses)} s without a breath seen; "
                               "holding still or a weak signal can also cause this")
    return out


def estimate(signals: dict, fs: float) -> dict:
    out = {}
    for name, x in signals.items():
        if x is None or len(x) < MIN_SECONDS * fs:
            continue
        brpm, snr = rr_spectrum(x, fs)
        out[name] = {"brpm": round(brpm, 1), "snr_db": round(snr, 1)}
    return out
