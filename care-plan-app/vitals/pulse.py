"""Pulse extraction from per-frame mean RGB: POS / CHROM, band-pass, heart-rate estimate. CPU only."""
from __future__ import annotations

import numpy as np
from scipy.signal import butter, filtfilt, detrend

HR_LOW_HZ, HR_HIGH_HZ = 0.7, 3.0          # 42-180 bpm


def resample_uniform(t: np.ndarray, rgb: np.ndarray, fs: float = 30.0):
    """Webcam and phone clips have uneven frame times; interpolate onto a fixed grid."""
    t = np.asarray(t, float)
    grid = np.arange(t[0], t[-1], 1.0 / fs)
    out = np.stack([np.interp(grid, t, rgb[:, c]) for c in range(3)], axis=1)
    return grid, out


def bandpass(x: np.ndarray, fs: float, lo: float = HR_LOW_HZ, hi: float = HR_HIGH_HZ, order: int = 3) -> np.ndarray:
    b, a = butter(order, [lo / (fs / 2), hi / (fs / 2)], btype="band")
    return filtfilt(b, a, detrend(x))


def pos(rgb: np.ndarray, fs: float, win_s: float = 1.6) -> np.ndarray:
    """Plane-Orthogonal-to-Skin (Wang 2017). rgb: (n, 3) in R, G, B order."""
    n = len(rgb)
    l = max(int(round(win_s * fs)), 8)
    h = np.zeros(n)
    proj = np.array([[0.0, 1.0, -1.0], [-2.0, 1.0, 1.0]])
    for start in range(0, n - l + 1):
        seg = rgb[start:start + l]
        cn = seg / np.maximum(seg.mean(axis=0), 1e-6)
        s = proj @ cn.T
        sd2 = s[1].std()
        w = s[0] + (s[0].std() / sd2) * s[1] if sd2 > 1e-9 else s[0]
        h[start:start + l] += w - w.mean()
    return h


def chrom(rgb: np.ndarray, fs: float) -> np.ndarray:
    """Chrominance method (de Haan 2013), whole-clip version."""
    cn = rgb / np.maximum(rgb.mean(axis=0), 1e-6)
    xs = bandpass(3 * cn[:, 0] - 2 * cn[:, 1], fs)
    ys = bandpass(1.5 * cn[:, 0] + cn[:, 1] - 1.5 * cn[:, 2], fs)
    alpha = xs.std() / max(ys.std(), 1e-9)
    return xs - alpha * ys


def spectrum_peak(x: np.ndarray, fs: float, nfft: int = 8192):
    """Dominant frequency in the heart band and a signal-to-noise ratio in dB."""
    x = (x - x.mean()) * np.hanning(len(x))
    f = np.fft.rfftfreq(nfft, 1.0 / fs)
    p = np.abs(np.fft.rfft(x, nfft)) ** 2
    band = (f >= HR_LOW_HZ) & (f <= HR_HIGH_HZ)
    fb, pb = f[band], p[band]
    k = int(np.argmax(pb))
    peak = fb[k]
    near = (np.abs(fb - peak) <= 0.1) | (np.abs(fb - 2 * peak) <= 0.1)
    signal, noise = pb[near].sum(), pb[~near].sum()
    return float(peak), float(10 * np.log10(max(signal, 1e-12) / max(noise, 1e-12)))


def heart_rate(bvp: np.ndarray, fs: float, win_s: float = 10.0, step_s: float = 1.0):
    """Sliding-window HR in bpm. Returns (window_centres_s, bpm, snr_db)."""
    w, s = int(win_s * fs), int(step_s * fs)
    if len(bvp) < w:
        raise ValueError(f"clip too short: need {win_s:.0f}s of usable signal, have {len(bvp) / fs:.1f}s")
    centres, bpm, snr = [], [], []
    for a in range(0, len(bvp) - w + 1, s):
        peak, q = spectrum_peak(bvp[a:a + w], fs)
        centres.append((a + w / 2) / fs)
        bpm.append(peak * 60.0)
        snr.append(q)
    return np.array(centres), np.array(bpm), np.array(snr)
