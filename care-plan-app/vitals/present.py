"""The analysis result turned into what the page shows: one card per reading, each with a value, how sure it is, and
what to keep in mind. Every reading gives a best guess (marked low confidence when weak) except SpO2, which needs an
oximeter calibration before it can be a percentage: a made-up figure there would hide a dangerous low."""
from __future__ import annotations

import math

import numpy as np

# good: agreed and checked; approximate: usable, not validated on this person; low: a best guess, can be well off
HR_CONFIDENCE = {"reliable": "good", "approximate": "approximate", "uncertain": "low", "low confidence": "low"}


def jsonable(x):
    """numpy scalars/arrays and NaN into plain JSON values."""
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    if isinstance(x, np.ndarray):
        return jsonable(x.tolist())
    if isinstance(x, np.generic):
        x = x.item()
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return x


def _conf(d: dict, normal: str = "approximate") -> str:
    return "low" if (d or {}).get("confidence") == "low" else normal


def _card(key, title, value, unit="", summary="", confidence="approximate", warnings=(), note="", sub=None):
    return {"key": key, "title": title, "value": value, "unit": unit, "summary": summary, "confidence": confidence,
            "warnings": list(warnings or []), "note": note, "sub": sub}


def heart(res: dict) -> dict:
    hr, avg = res.get("hr_bpm"), res.get("weighted_average") or {}
    if hr is None:
        return _card("heart", "Heart rate", None, "bpm", "No face or pulse was found in the video.", "none",
                     res.get("warnings"))
    n = len(avg.get("kept") or [])
    summary = f"Average of {n} methods" + (f", within ±{avg['spread']:.0f} bpm of each other" if avg.get("spread") is not None else "")
    return _card("heart", "Heart rate", round(hr), "bpm", summary, HR_CONFIDENCE.get(res.get("hr_level"), "low"),
                 res.get("warnings"), "Checked on 42 people against a finger sensor: about 2-3 bpm off on phone-quality video.")


def breathing(b: dict) -> dict:
    if not b or b.get("rr_brpm") is None:
        return _card("breathing", "Breathing rate", None, "/min", "No breathing movement was found.", "none",
                     (b or {}).get("warnings"))
    c = b["consensus"]
    summary = (f"{c['n_agree']} of {c['n_estimates']} sources agree ({', '.join(c['agree'])})" if c.get("agree")
               else "from the clearest single source")
    return _card("breathing", "Breathing rate", round(b["rr_brpm"]), "breaths/min", summary, _conf(b), b["warnings"], b["note"])


def pattern(b: dict) -> dict:
    p = (b or {}).get("pattern") or {}
    if not p.get("level"):
        return _card("pattern", "Breathing pattern", None, "", "Too few breaths to judge the pattern.", "none", p.get("warnings"))
    bits = [f"breath-to-breath variation {p['variability']:.0%}", f"{p['breaths']} breaths"]
    if p.get("pauses_s"):
        bits.append(f"{len(p['pauses_s'])} pause(s)")
    if p.get("sighs"):
        bits.append(f"{p['sighs']} deep breath(s)")
    return _card("pattern", "Breathing pattern", p["level"], "", ", ".join(bits), _conf(p), p.get("warnings"),
                 "Pauses and deep sighs are normal now and then; it is the pattern over minutes that matters.")


def hrv(h: dict) -> dict:
    if not h or h.get("sdnn_ms") is None:
        return _card("hrv", "Heart-rate variability", None, "ms", "Not enough clear beats.", "none", (h or {}).get("warnings"))
    r = h.get("range_ms") or {}
    sd = r.get("sdnn")
    summary = (f"{h['band']}" if h.get("band") else "") + (f"; likely {sd[0]:.0f}-{sd[1]:.0f} ms" if sd else "")
    sub = {"label": "Beat-to-beat (RMSSD)", "value": f"{h['rmssd_ms']:.0f} ms"} if h.get("rmssd_ms") is not None else None
    conf = "low" if h.get("level") == "low confidence" else "approximate"
    return _card("hrv", "Heart-rate variability", round(h["sdnn_ms"]), "ms SDNN", summary.strip("; "), conf,
                 h.get("warnings"), h.get("note", ""), sub)


def rhythm(r: dict) -> dict:
    if not r or not r.get("level"):
        return _card("rhythm", "Pulse rhythm", None, "", "Needs clear beat timing.", "none", (r or {}).get("warnings"),
                     (r or {}).get("note", ""))
    return _card("rhythm", "Pulse rhythm", r["level"], "", "How even the gaps between beats look in this video",
                 _conf(r, "good" if r["level"] == "steady" else "approximate"), r.get("warnings"), r.get("note", ""))


def strength(ps: dict) -> dict:
    if not ps or ps.get("pct") is None:
        return _card("strength", "Pulse strength", None, "", "The pulse was too faint to measure.", "none", (ps or {}).get("warnings"))
    return _card("strength", "Pulse strength", f"{ps['pct']:.2f}", "% of skin brightness",
                 "How much the skin colour changes with each beat; compare only with your own earlier readings",
                 _conf(ps), ps.get("warnings"), ps.get("note", ""))


def eyes(e: dict) -> dict:
    if not e or not e.get("state"):
        return _card("eyes", "Alertness (eyes)", None, "", "The eyes could not be followed.", "none", (e or {}).get("warnings"))
    rate = e["blinks_per_min"]
    summary = (f"{rate:.0f} blink{'' if round(rate) == 1 else 's'} a minute, {e['rate_band']}; "
               f"eyes mostly closed {e['perclos']:.0%} of the time")
    return _card("eyes", "Alertness (eyes)", e["state"], "", summary, _conf(e), e.get("warnings"), e.get("note", ""))


def stress(s: dict) -> dict:
    if not s or not s.get("level"):
        return _card("stress", "Stress level", None, "", "Needs a heart rate.", "none", (s or {}).get("warnings"))
    approx = any("approximate" in w or "rougher" in w for w in s.get("warnings", []))
    return _card("stress", "Stress level", s["level"], "", "; ".join(s["reasons"]), "low" if approx else "approximate",
                 s.get("warnings"), s.get("note", ""), {"label": "Based on", "value": ", ".join(s["based_on"])})


def spo2(o: dict) -> dict:
    o = o or {}
    if o.get("spo2_pct") is not None:
        return _card("spo2", "Blood oxygen (SpO2)", round(o["spo2_pct"]), "%", "From the calibrated colour ratio",
                     "approximate", o.get("warnings"), o.get("note", ""))
    return _card("spo2", "Blood oxygen (SpO2)", None, "", "Not shown until calibrated with a fingertip oximeter", "none",
                 [], "A guessed percentage would read about 97% for everyone, including someone who needs help, so "
                     "none is shown. Use a fingertip oximeter for oxygen.")


def quality(res: dict, video: dict | None) -> list[dict]:
    rows = [("Length", f"{res.get('duration_s', 0):.0f} s"),
            ("Face found in", f"{res.get('face_fraction', 0):.0%} of frames")]
    if video:
        rows.append(("Camera frame rate", f"{video['fps']:.0f} fps" + (" (re-timed to 30)" if not video["steady"] else "")))
    if res.get("skin_brightness") is not None:
        rows.append(("Light on the face", "good" if res["skin_brightness"] >= 90 else "dim" if res["skin_brightness"] >= 60 else "too dark"))
    mo = res.get("motion")
    if mo:
        rows.append(("Moving", f"{mo['fraction']:.0%} of the time set aside"))
    return [{"label": a, "value": b} for a, b in rows]


def present(res: dict, video: dict | None = None) -> dict:
    res = jsonable(res)
    b = res.get("breathing") or {}
    return {
        "heart": heart(res),
        "cards": [breathing(b), pattern(b), hrv(res.get("hrv")), rhythm(res.get("rhythm")), strength(res.get("pulse_strength")),
                  eyes(res.get("eyes")), stress(res.get("stress")), spo2(res.get("spo2"))],
        "quality": quality(res, video),
        "disclaimer": res.get("disclaimer"),
    }
