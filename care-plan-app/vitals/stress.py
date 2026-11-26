"""A stress/relaxation indicator from heart rate, HRV and breathing. Heuristic, not a measurement: the cut-offs are
rough population figures and a person's own baseline matters more than any fixed threshold.

With the phone model's rough HRV, SDNN is used, by its band (on UBFC it beat guessing; RMSSD did not). Without any HRV
the indicator still runs on heart rate and breathing, marked as such."""
from __future__ import annotations

RELAXED = {"rmssd_min": 40.0, "hr_max": 80.0, "rr_max": 16.0}
ELEVATED = {"rmssd_max": 20.0, "hr_min": 95.0, "rr_min": 22.0}


def stress(hr_bpm: float | None, rmssd_ms: float | None, rr_brpm: float | None, hrv: dict | None = None) -> dict:
    out = {"level": None, "reasons": [], "warnings": [], "based_on": [],
           "note": "Heuristic wellness indicator; compare with your own calm baseline rather than the fixed cut-offs."}
    if hr_bpm is None:
        out["warnings"].append("needs a heart rate")
        return out
    band = (hrv or {}).get("band") if (hrv or {}).get("level") in ("rough", "low confidence") else None
    low_hrv = high_hrv = None
    if band:
        low_hrv, high_hrv = band == "lower than typical", band != "lower than typical"
        hrv_text = f"HRV {band} (SDNN about {hrv['sdnn_ms']:.0f} ms)"
        out["based_on"].append("rough HRV")
    elif rmssd_ms is not None:
        low_hrv, high_hrv = rmssd_ms < ELEVATED["rmssd_max"], rmssd_ms >= RELAXED["rmssd_min"]
        hrv_text = f"HRV {rmssd_ms:.0f} ms"
        out["based_on"].append("HRV")
    out["based_on"].append("heart rate")
    if rr_brpm is not None:
        out["based_on"].append("breathing")
    elevated = []
    if low_hrv:
        elevated.append("low " + hrv_text)
    if hr_bpm > ELEVATED["hr_min"]:
        elevated.append(f"high resting heart rate ({hr_bpm:.0f} bpm)")
    if rr_brpm is not None and rr_brpm > ELEVATED["rr_min"]:
        elevated.append(f"fast breathing ({rr_brpm:.0f}/min)")
    relaxed = ((low_hrv is None or high_hrv) and hr_bpm <= RELAXED["hr_max"]
               and (rr_brpm is None or rr_brpm <= RELAXED["rr_max"]))
    if elevated:
        out["level"], out["reasons"] = "elevated", elevated
    elif relaxed:
        out["level"] = "relaxed"
        parts = ([hrv_text] if low_hrv is not None else []) + [f"heart rate {hr_bpm:.0f} bpm"]
        if rr_brpm is not None:
            parts.append(f"breathing {rr_brpm:.0f}/min")
        out["reasons"] = [", ".join(parts)]
    else:
        out["level"], out["reasons"] = "moderate", ["between the relaxed and elevated ranges"]
    if low_hrv is None:
        out["warnings"].append("no HRV from this clip; judged on heart rate" + (" and breathing" if rr_brpm is not None else "")
                               + " only, so it is rougher")
    if rr_brpm is None:
        out["warnings"].append("breathing rate not available")
    return out
