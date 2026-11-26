"""Quality-weighted fusion of many pulse estimators (classical methods, face patches, pretrained models).

Every 8 s window, each estimator's pulse spectrum is weighted by how clearly its own peak stands out (SNR) and the
weighted spectra are added. The heart rate is the peak of that sum; windows count by how clear the summed peak is.
This replaces "one estimator, one vote", which split 4-4 on a real clip."""
from __future__ import annotations

import numpy as np
from scipy.signal import butter, detrend, filtfilt

from .pulse import pos, resample_uniform

BAND = (0.7, 3.0)
WIN_S, STEP_S, NFFT = 8.0, 2.0, 4096
AGREE_BPM = 5.0
RELIABLE_SUPPORT, APPROX_SUPPORT = 0.6, 0.4
RELIABLE_DOMINANCE, APPROX_DOMINANCE = 0.65, 0.55
RIVAL_MIN_HZ = 0.3


def bandpass(x, fs, lo=BAND[0], hi=BAND[1]):
    b, a = butter(3, [lo / (fs / 2), hi / (fs / 2)], btype="band")
    return filtfilt(b, a, detrend(x))


def window_spectra(x: np.ndarray, fs: float):
    """Normalised in-band power spectrum of each window: (freqs, array[n_windows, n_bins])."""
    x = bandpass(x, fs)
    w, s = int(WIN_S * fs), int(STEP_S * fs)
    f = np.fft.rfftfreq(NFFT, 1 / fs)
    band = (f >= BAND[0]) & (f <= BAND[1])
    rows = []
    for i in range(0, len(x) - w + 1, s):
        seg = (x[i:i + w] - x[i:i + w].mean()) * np.hanning(w)
        p = (np.abs(np.fft.rfft(seg, NFFT)) ** 2)[band]
        rows.append(p / max(p.sum(), 1e-20))
    return f[band], np.array(rows)


def snr_db(fb: np.ndarray, p: np.ndarray, f0: float | None = None) -> float:
    """Power at the peak (or at f0) and its harmonic, against everything else in the band."""
    if f0 is None:
        f0 = fb[int(np.argmax(p))]
    near = (np.abs(fb - f0) <= 0.1) | (np.abs(fb - 2 * f0) <= 0.1)
    return float(10 * np.log10(max(p[near].sum(), 1e-20) / max(p[~near].sum(), 1e-20)))


def _weight(db: float) -> float:
    return float(np.clip(10 ** (db / 10), 0.05, 20.0))


def fuse_spectra(spectra: dict):
    """Weighted sum per window. spectra: name -> (freqs, P). Returns freqs, fused P, per-name mean weight share."""
    spectra = {k: v for k, v in spectra.items() if len(v[1])}
    if not spectra:
        return None, np.zeros((0, 0)), {}
    fb = next(iter(spectra.values()))[0]
    n = min(len(P) for _, P in spectra.values())
    fused = np.zeros((n, len(fb)))
    share = {k: 0.0 for k in spectra}
    for j in range(n):
        ws = {k: _weight(snr_db(fb, P[j])) for k, (_, P) in spectra.items()}
        tot = sum(ws.values())
        for k, (_, P) in spectra.items():
            fused[j] += ws[k] / tot * P[j]
            share[k] += ws[k] / tot / n
    return fb, fused, share


def patches_spectra(trace: dict, fs: float = 30.0):
    """The face patches as ONE estimator: each patch's POS spectrum weighted by its own clarity, per window."""
    pt = trace.get("patches")
    if pt is None or len(pt) < 2:
        return None
    t = trace["t"]
    per = {}
    for i in range(pt.shape[1]):
        rgb = pt[:, i, :]
        ok = np.isfinite(rgb).all(axis=1)
        if ok.mean() < 0.8:
            continue
        _, even = resample_uniform(t[ok], rgb[ok], fs)
        if len(even) < WIN_S * fs:
            continue
        per[i] = window_spectra(pos(even, fs), fs)
    if not per:
        return None
    fb, fused, _ = fuse_spectra(per)
    return fb, fused


def patches_signal(trace: dict, f0: float, fs: float = 30.0) -> np.ndarray | None:
    """One pulse waveform from the patches, each weighted by how clearly it shows the heart rate f0 over the clip."""
    pt = trace.get("patches")
    if pt is None:
        return None
    t = trace["t"]
    sigs, ws = [], []
    for i in range(pt.shape[1]):
        rgb = pt[:, i, :]
        ok = np.isfinite(rgb).all(axis=1)
        if ok.mean() < 0.8:
            continue
        _, even = resample_uniform(t[ok], rgb[ok], fs)
        x = pos(even, fs)
        fb, P = whole_spectrum(x, fs)
        sigs.append(x)
        ws.append(_weight(snr_db(fb, P, f0)))
    if not sigs:
        return None
    return weighted_combine(sigs, ws)


def whole_spectrum(x: np.ndarray, fs: float):
    x = bandpass(x, fs)
    f = np.fft.rfftfreq(max(NFFT, len(x)), 1 / fs)
    band = (f >= BAND[0]) & (f <= BAND[1])
    p = (np.abs(np.fft.rfft((x - x.mean()) * np.hanning(len(x)), max(NFFT, len(x)))) ** 2)[band]
    return f[band], p / max(p.sum(), 1e-20)


def weighted_combine(signals: list, weights: list) -> np.ndarray:
    """z-score, align signs to the strongest, weighted average; trimmed to the shortest."""
    n = min(len(s) for s in signals)
    zs = [(s[:n] - s[:n].mean()) / (s[:n].std() or 1.0) for s in signals]
    ref = zs[int(np.argmax(weights))]
    out = np.zeros(n)
    for z, w in zip(zs, weights):
        out += w * (z if np.dot(z, ref) >= 0 else -z)
    return out / max(sum(weights), 1e-12)


def dominance(fb: np.ndarray, p: np.ndarray, f0: float) -> float:
    """Power at f0 (and its harmonic) against the strongest rival peak that is neither f0, its harmonic nor half of it.
    One clear rhythm -> near 1; two equally strong rhythms -> 0.5."""
    df = fb[1] - fb[0]
    k = max(1, int(round(0.1 / df)))
    smooth = np.convolve(p, np.ones(2 * k + 1), mode="same")
    main = smooth[int(np.argmin(np.abs(fb - f0)))]
    if fb[-1] >= 2 * f0:
        main += smooth[int(np.argmin(np.abs(fb - 2 * f0)))]
    # An 8 s window spreads every peak over about ±0.25 Hz, so a rival must sit beyond that skirt to be a real rival;
    # closer disagreement shows up in the window-agreement score instead.
    rival_zone = (np.abs(fb - f0) > RIVAL_MIN_HZ) & (np.abs(fb - 2 * f0) > RIVAL_MIN_HZ) & (np.abs(fb - f0 / 2) > RIVAL_MIN_HZ)
    rival = smooth[rival_zone].max() if rival_zone.any() else 0.0
    return float(main / max(main + rival, 1e-20))


def heart_rate(spectra: dict, mask: np.ndarray | None = None, fs: float = 30.0) -> dict:
    """Fused heart rate with a support score: the share of window weight that agrees with the answer.
    mask: per-sample True where movement or talking was found; windows mostly inside such stretches are left out."""
    fb, fused, share = fuse_spectra(spectra)
    out = {"hr": None, "level": None, "support": 0.0, "windows": int(len(fused)), "estimators": {}}
    if fb is None or not len(fused):
        return out
    hr_w = fb[np.argmax(fused, axis=1)] * 60
    q_w = np.array([_weight(snr_db(fb, p)) for p in fused])
    if mask is not None:
        from .motion import window_factors
        q_w = q_w * window_factors(mask, fs, WIN_S, STEP_S, len(fused))
        out["windows_used"] = int((q_w > 0).sum())
        if not q_w.any():
            out["warnings"] = ["every window had movement or talking"]
            return out
    # Average the fused spectra over all windows before picking the peak (Welch): a weak but steady pulse adds up,
    # noise that wanders from window to window does not.
    welch = np.average(fused, axis=0, weights=q_w)
    hr = float(fb[int(np.argmax(welch))] * 60)
    support = float(q_w[np.abs(hr_w - hr) <= AGREE_BPM].sum() / q_w.sum())
    for k, (f, P) in spectra.items():
        if len(P):
            own = f[np.argmax(P, axis=1)] * 60
            out["estimators"][k] = {"bpm": round(float(np.median(own)), 1), "weight": round(share.get(k, 0.0), 3),
                                    "snr_db": round(float(np.median([snr_db(f, p) for p in P])), 1)}
    dom = dominance(fb, welch, hr / 60)
    level = ("reliable" if support >= RELIABLE_SUPPORT and dom >= RELIABLE_DOMINANCE else
             "approximate" if support >= APPROX_SUPPORT and dom >= APPROX_DOMINANCE else None)
    out.update(hr=round(hr, 1) if level else None, candidate=round(hr, 1), level=level, support=round(support, 2),
               dominance=round(dom, 2),
               window_hr=[round(float(x), 1) for x in hr_w])
    return out


def plausible_average(estimates: dict, low: float, high: float, outlier_bpm: float = 8.0, split_bpm: float = 12.0) -> dict:
    """Weighted average of estimator readings after dropping (1) readings outside the plausible range for the person's
    situation and (2) readings far from the weighted median. estimates: name -> (bpm, weight).
    Flags when what is left still falls into two separate groups, because then the average sits between two answers
    that neither group supports."""
    dropped, kept = {}, {}
    for k, (bpm, w) in estimates.items():
        if not (low <= bpm <= high):
            dropped[k] = f"{bpm:.0f} outside {low:.0f}-{high:.0f}"
        else:
            kept[k] = (bpm, w)
    out = {"hr": None, "kept": {}, "dropped": dropped, "split": None}
    if not kept:
        return out
    v = np.array([b for b, _ in kept.values()])
    w = np.array([x for _, x in kept.values()])
    order = np.argsort(v)
    cw = np.cumsum(w[order])
    med = float(v[order][np.searchsorted(cw, cw[-1] / 2)])
    mad = float(np.average(np.abs(v - med), weights=w))
    limit = max(outlier_bpm, 2.5 * mad)
    for k, (bpm, wt) in list(kept.items()):
        if abs(bpm - med) > limit:
            dropped[k] = f"{bpm:.0f} far from the others (median {med:.0f})"
            del kept[k]
    v = np.array([b for b, _ in kept.values()])
    w = np.array([x for _, x in kept.values()])
    hr = float(np.average(v, weights=w))
    near = float(w[np.abs(v - hr) <= AGREE_BPM].sum() / w.sum())
    out.update(hr=round(hr, 1), kept={k: round(b, 1) for k, (b, _) in kept.items()},
               spread=round(float(np.sqrt(np.average((v - hr) ** 2, weights=w))), 1),
               support_at_average=round(near, 2))
    s = np.sort(v)
    if len(s) >= 2:
        gaps = np.diff(s)
        i = int(np.argmax(gaps))
        if gaps[i] >= split_bpm:
            lo_side = v <= s[i]
            share = float(min(w[lo_side].sum(), w[~lo_side].sum()) / w.sum())
            if share >= 0.3:
                out["split"] = {"low_group": round(float(np.average(v[lo_side], weights=w[lo_side])), 1),
                                "high_group": round(float(np.average(v[~lo_side], weights=w[~lo_side])), 1),
                                "smaller_share": round(share, 2)}
    return out
