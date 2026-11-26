import argparse
import json
import os
import sys

import numpy as np

from .analyze import analyze
from .resp import analyze_breathing
from .trace import load_trace, record_trace, save_trace, trace_from_video


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vitals")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record", help="record from the webcam and save a trace")
    r.add_argument("--seconds", type=float, default=40)
    r.add_argument("--camera", type=int, default=0)
    r.add_argument("--out", default=None, help="trace file (.npz); default clips/<n>.npz")
    r.add_argument("--analyze", action="store_true", help="analyze right after recording")
    a = sub.add_parser("analyze", help="analyze a video (classical + pretrained models, agreement vote) or a trace (.npz, classical only)")
    a.add_argument("path")
    a.add_argument("--ref", type=float, default=None, help="reference heart rate (watch/oximeter) to compare against")
    a.add_argument("--ref-rr", type=float, default=None, help="reference breathing rate (breaths/min you counted or paced)")
    a.add_argument("--fast", action="store_true", help="classical methods only, skip the pretrained models")
    a.add_argument("--models", default=None, help="comma-separated open-rppg models (default: the standard seven)")
    a.add_argument("--no-context", action="store_true", help="skip the Gemini posture/activity check")
    a.add_argument("--env-file", default=None, help="a .env file to read GEMINI_API_KEY from")
    a.add_argument("--json", action="store_true")
    ch = sub.add_parser("check", help="check a clip against the capture spec and say what to fix (no analysis)")
    ch.add_argument("path", nargs="?", help="video to check; omit to print the recording instructions")
    k = sub.add_parser("calibrate", help="SpO2 calibration: add a clip with its oximeter reading, or show the current fit")
    k.add_argument("action", choices=["add", "show"])
    k.add_argument("path", nargs="?")
    k.add_argument("--spo2", type=float, default=None, help="oximeter reading (%%) taken during the clip")
    c = sub.add_parser("camera-check", help="measure what the webcam delivers (fps, steadiness) per pixel format, no face detection")
    c.add_argument("--camera", type=int, default=0)
    c.add_argument("--seconds", type=float, default=6)
    args = ap.parse_args(argv)

    if args.cmd == "camera-check":
        from .trace import camera_check
        print("Sit as you would for a recording, with the lamp on. About 25 seconds.")
        for row in camera_check(args.camera, args.seconds):
            print(f"{row['requested']:8s} got {row['got']:5s} {row['fps']:5.1f} fps   frame gap median {row['median_ms']} ms, "
                  f"p95 {row['p95_ms']} ms, worst {row['max_ms']} ms   brightness {row['brightness']} (range {row['brightness_range']})")
        return 0

    if args.cmd == "record":
        tr = record_trace(args.seconds, args.camera)
        if tr is None:
            print("cancelled")
            return 1
        out = args.out
        if not out:
            os.makedirs("clips", exist_ok=True)
            n = 1
            while os.path.exists(f"clips/clip{n}.npz"):
                n += 1
            out = f"clips/clip{n}.npz"
        save_trace(out, tr)
        print(f"saved {out}  (dropped frames: {tr['meta'].get('dropped_frames')})")
        if not args.analyze:
            return 0
        res = run_analysis(out, fast=True)
        report(res, None, None, False)
        return 0

    if args.cmd == "check":
        from .capture import SPEC_TEXT, check
        if not args.path:
            print(SPEC_TEXT)
            return 0
        r = check(args.path)
        print(", ".join(f"{k} {v}" for k, v in r["facts"].items()))
        print("GOOD TO ANALYSE" if r["ok"] else "TO FIX:")
        for i in r["issues"]:
            print("  - " + i)
        return 0 if r["ok"] else 1

    if args.cmd == "calibrate":
        from .spo2 import CAL_PATH, add_point, load_calibration
        if args.action == "show":
            cal = load_calibration()
            print(json.dumps(cal, indent=2))
            return 0
        if not args.path or args.spo2 is None:
            print("usage: vitals calibrate add VIDEO --spo2 <oximeter reading during the clip>")
            return 2
        res = run_analysis(args.path, fast=False, models_arg=None, log=print)
        ratio = res["spo2"].get("ratio_rb")
        if ratio is None:
            print("This clip gave no usable SpO2 ratio, so it cannot be a calibration point:")
            for w in res["spo2"]["warnings"]:
                print("  ! " + w)
            return 1
        n = add_point(ratio, args.spo2, os.path.abspath(args.path))
        cal = load_calibration()
        print(f"added point {n}: ratio {ratio} at {args.spo2}%  ->  saved in {CAL_PATH}")
        print("calibration usable" if cal["usable"] else f"calibration not usable yet: {cal['reason']}")
        return 0

    if args.env_file and not os.environ.get("GEMINI_API_KEY"):
        load_key(args.env_file)
    res = run_analysis(args.path, args.fast, args.models, (lambda *_: None) if args.json else print,
                       use_context=not args.no_context)
    report(res, args.ref, args.ref_rr, args.json)
    return 0


def load_key(env_file):
    with open(env_file, encoding="utf-8") as f:
        for line in f:
            if line.startswith("GEMINI_API_KEY="):
                os.environ["GEMINI_API_KEY"] = line.split("=", 1)[1].strip().strip('"').strip("'")
                return


def run_analysis(path, fast=False, models_arg=None, log=print, use_context=False):
    from .spo2 import spo2_index
    from .stress import stress
    is_trace = path.lower().endswith(".npz")
    log("classical methods (face tracking)...")
    tr = load_trace(path) if is_trace else trace_from_video(path)
    keys = None
    if not is_trace:
        from .codec import clean_trace, gop_length, keyframe_index
        keys = keyframe_index(path)
        tr = clean_trace(tr, keys)
    res = analyze(tr)
    from .motion import motion_mask
    mm = motion_mask(tr)
    res["motion"] = {k: v for k, v in mm.items() if k != "mask"}
    mask = mm["mask"]
    models = None
    if not is_trace and not fast:
        from .deep import run_models
        log("pretrained models (CPU)...")
        models = run_models(path, models_arg.split(",") if models_arg else None, log=log)
    if not is_trace:
        from . import phone_model
        if phone_model.available():           # cheap (~0.2 s per 6 s of video), so it runs in --fast too
            log("phone-video model (CPU)...")
            models = models or {}
            models[phone_model.NAME] = phone_model.run(path, log=log)
    if models:
        if keys:
            from .codec import remove_keyframe_pattern
            for m in models.values():
                if "raw" in m:
                    m["raw"] = remove_keyframe_pattern(m["raw"], keys)
        res["models"] = {k: {kk: (round(vv, 1) if isinstance(vv, float) else vv) for kk, vv in v.items()
                             if kk not in ("bvp", "raw", "fs", "slope")} for k, v in models.items()}
    pulse_fusion(res, tr, models, mask)
    plausible_hr(res, path if (use_context and not is_trace) else None, log)
    hr = res["hr_bpm"]
    gop = gop_length(keys) if keys else None
    if gop:
        key_bpm = 60.0 * 30.0 / gop
        res["keyframe_bpm"] = round(key_bpm, 1)
        if hr is not None and abs(hr - key_bpm) <= 3:
            res["warnings"].append(f"heart rate is at the video's keyframe rhythm ({key_bpm:.0f}/min) and cannot be told apart from it")
    agreed = [n for n, e in res["fusion"]["estimators"].items()
              if models and n in models and hr is not None and abs(e["bpm"] - hr) <= 5.0]
    res["breathing"] = analyze_breathing(tr, models, agreed or None, mask=mask)
    from .hrv import estimate, hrv
    from .phone_model import NAME as PHONE
    pm = (models or {}).get(PHONE, {})
    if "slope" in pm:
        from .phone_model import meta
        mt = meta()
        res["hrv"] = estimate(pm["slope"], pm["fs"], hr, mt["hrv_noise_var_ms2"], mt["hrv_error_ms"], mask)
        res["hrv"]["from"] = PHONE
    elif hr is not None:
        sig, used = hrv_pulse(tr, models, hr)
        res["hrv"] = hrv(sig, 30.0, hr, mask)
        res["hrv"]["from"] = used
        res["hrv"]["warnings"].append("phone model not used, so beat timing noise is not removed: HRV likely reads high")
    else:
        res["hrv"] = hrv(np.zeros(1), 30.0, None)
    if "slope" in pm:
        from .rhythm import regularity
        res["rhythm"] = regularity(pm["slope"], pm["fs"], hr, mt["hrv_noise_var_ms2"], mask)
    else:
        res["rhythm"] = {"level": None, "warnings": ["needs the phone-video model's beat timing (video input)"],
                         "note": "A flag, not a diagnosis."}
    from .eyes import eyes
    from .strength import pulse_strength
    res["pulse_strength"] = pulse_strength(tr, hr, mask=mask)
    res["eyes"] = eyes(tr)
    res["spo2"] = spo2_index(tr, res["hr_bpm"], mask=mask)
    res["stress"] = stress(res["hr_bpm"], (res.get("hrv") or {}).get("rmssd_ms"), res["breathing"]["rr_brpm"], res.get("hrv"))
    if res["stress"]["level"] and {"approximate", "uncertain", "rough"} & {res["hr_level"], res["hrv"].get("level")}:
        res["stress"]["warnings"].append("based on approximate heart-rate or HRV readings")
    return res


def classical_signals(tr, fs=30.0):
    from .pulse import chrom, pos, resample_uniform
    rgb, ok = tr["rgb"], np.isfinite(tr["rgb"]).all(axis=1)
    if ok.sum() < 2:
        return {}
    _, even = resample_uniform(tr["t"][ok], rgb[ok], fs)
    return {"pos": pos(even, fs), "chrom": chrom(even, fs)}


def pulse_fusion(res, tr, models, mask=None):
    """Heart rate from every estimator at once, each weighted by its own signal quality per window."""
    from .fusion import heart_rate, patches_spectra, window_spectra
    spectra = {k: window_spectra(x, 30.0) for k, x in classical_signals(tr).items() if len(x) >= 8 * 30}
    ps = patches_spectra(tr)
    if ps is not None:
        spectra["face_patches"] = ps
    for k, m in (models or {}).items():
        if "raw" in m:
            spectra[k] = window_spectra(m["raw"], m["fs"])
    grid_mask = None
    if mask is not None and len(tr["t"]) > 1:
        from .motion import resample_mask
        grid_mask = resample_mask(mask, int(round((tr["t"][-1] - tr["t"][0]) * 30.0)))
    f = heart_rate(spectra, grid_mask)
    res["fusion"] = f
    res["hr_bpm"], res["hr_level"], res["reliable"] = f["hr"], f["level"], f["level"] == "reliable"
    keep = ("too short", "face found", "too dark", "movement", "fps")      # about the recording, so they still apply
    res["warnings"] = [w for w in res["warnings"] if any(k in w for k in keep)]
    if f["level"] == "approximate":
        res["warnings"].append(f"approximate: only {f['support']:.0%} of the signal agrees on this heart rate")
    elif f["hr"] is None and f.get("candidate"):
        res["warnings"].append(f"only {f['support']:.0%} of the signal agrees on the best guess ({f['candidate']:.0f} bpm); need 40%")


MIN_SUPPORT_AT_AVERAGE = 0.3


def plausible_hr(res, video_path, log=print):
    """Headline heart rate: weighted average of the estimators that are plausible for what the person is doing."""
    from .context import describe, plausible_range
    from .fusion import plausible_average
    ctx = None
    if video_path:
        log("situation check (Gemini, 8 still frames)...")
        ctx = describe(video_path)
    rng = plausible_range(ctx)
    est = {k: (e["bpm"], e["weight"]) for k, e in res["fusion"]["estimators"].items()}
    avg = plausible_average(est, rng["low"], rng["high"])
    res["context"], res["plausible_range"], res["weighted_average"] = ctx, rng, avg
    if avg["hr"] is None:
        # Best guess: the spectral fusion's strongest rate, else the heaviest single estimate.
        fused = res["fusion"]
        best = fused.get("candidate") or (max(est.values(), key=lambda e: e[1])[0] if est else None)
        res["hr_bpm"] = round(float(best), 1) if best is not None else None
        res["hr_level"], res["reliable"] = ("low confidence" if best is not None else None), False
        res["warnings"].append(f"no estimate falls in the plausible range {rng['low']}-{rng['high']} bpm; best guess shown")
        return
    # The fusion's own agreement notes are replaced by the ones below about the average.
    res["warnings"] = [w for w in res["warnings"] if not w.startswith(("approximate: only", "only "))]
    fused = res["fusion"]
    level = "reliable" if fused["level"] == "reliable" and not avg["split"] and abs(fused["candidate"] - avg["hr"]) <= 5 else "approximate"
    if avg["split"]:
        level = "uncertain"
        sp = avg["split"]
        res["warnings"].append(f"the plausible estimates form two groups ({sp['low_group']:.0f} and {sp['high_group']:.0f} bpm); "
                               "the average lies between them and neither group supports it")
    elif avg["support_at_average"] < MIN_SUPPORT_AT_AVERAGE:
        level = "uncertain"
        res["warnings"].append(f"only {avg['support_at_average']:.0%} of the estimate weight lies within ±5 bpm of the average; "
                               "the estimates disagree and the average may sit between them")
    res["hr_bpm"], res["hr_level"], res["reliable"] = avg["hr"], level, level == "reliable"


def hrv_pulse(tr, models, hr, fs=30.0):
    """One waveform for beat timing: every estimator weighted by how clearly it shows this heart rate."""
    from .fusion import _weight, patches_signal, snr_db, weighted_combine, whole_spectrum
    cands = dict(classical_signals(tr, fs))
    p = patches_signal(tr, hr / 60.0, fs)
    if p is not None:
        cands["face_patches"] = p
    for k, m in (models or {}).items():
        if "raw" in m:
            cands[k] = m["raw"]
    ws = {k: _weight(snr_db(*whole_spectrum(x, fs), hr / 60.0)) for k, x in cands.items()}
    top = sorted(ws, key=ws.get, reverse=True)
    return weighted_combine([cands[k] for k in top], [ws[k] for k in top]), {k: round(ws[k], 2) for k in top}


def report(res, ref, ref_rr, as_json):
    if as_json:
        print(json.dumps(res, indent=2))
        return
    print()
    print(f"duration {res['duration_s']}s, face in {res['face_fraction']:.0%} of frames, {res.get('measured_fps', '?')} fps")
    f = res.get("fusion", {})
    for k, e in sorted(f.get("estimators", {}).items(), key=lambda kv: -kv[1]["weight"]):
        print(f"  {k:20s} {e['bpm']:6.1f} bpm   SNR {e['snr_db']:5.1f} dB   weight {e['weight']:.0%}")
    for k, v in res.get("models", {}).items():
        if "error" in v:
            print(f"  {k:20s} failed: {v['error']}")
    mo = res.get("motion")
    if mo and mo["fraction"] > 0:
        why = ", ".join(f"{k} {v:.0%}" for k, v in mo["reasons"].items())
        print(f"set aside: {mo['fraction']:.0%} of the clip in {mo['bursts']} stretches ({why})")
    ctx, rng, avg = res.get("context"), res.get("plausible_range"), res.get("weighted_average")
    if ctx:
        print("situation: " + (f"{ctx.get('posture')}, {ctx.get('activity')}, {ctx.get('age_group')} "
                               f"({ctx.get('confidence')} confidence, {ctx.get('model')})" if "error" not in ctx else "unknown - " + ctx["error"]))
    if rng:
        print(f"plausible range: {rng['low']}-{rng['high']} bpm, typical {rng['typical']}  [{rng['basis']}]")
    if avg:
        for k, why in avg["dropped"].items():
            print(f"  dropped {k:20s} {why}")
        print(f"spectral fusion alone: {f.get('candidate')} bpm ({f.get('level') or 'no reading'}, {f.get('support', 0):.0%} agreement)")
    if res["hr_bpm"] is not None:
        line = f"HEART RATE: {res['hr_bpm']} bpm  ({res['hr_level']}; weighted average of {len(avg['kept'])} estimates, spread ±{avg['spread']}, {avg['support_at_average']:.0%} of weight within ±5)"
        if ref:
            line += f"   (reference {ref:.0f}, error {res['hr_bpm'] - ref:+.1f})"
        print(line)
    else:
        print("No heart rate: no face or pulse found.")
    for w in res["warnings"]:
        print("  ! " + w)
    b = res.get("breathing")
    if b:
        print()
        for k, v in b["methods"].items():
            extra = f"   (median of {v['models']} models)" if "models" in v else ""
            print(f"  {k:20s} {v['brpm']:6.1f} breaths/min   SNR {v['snr_db']:5.1f} dB{extra}")
        c = b["consensus"]
        if b["rr_brpm"] is not None:
            line = (f"BREATHING: {b['rr_brpm']} breaths/min  ({c['n_agree']} of {c['n_estimates']} sources agree within ±2: "
                    f"{', '.join(c['agree'])})" + ("  [LOW CONFIDENCE]" if b.get("confidence") == "low" else ""))
            if ref_rr:
                line += f"   (reference {ref_rr:.0f}, error {b['rr_brpm'] - ref_rr:+.1f})"
            print(line)
        else:
            print("No reliable breathing rate." + (f" Closest: {c['candidate']} breaths/min from {', '.join(c['agree'])}"
                                                    if c["candidate"] is not None else ""))
        for w in b["warnings"]:
            print("  ! " + w)
        print("  " + b["note"])
        p = b.get("pattern")
        if p and p["level"]:
            extra = (f", {len(p['pauses_s'])} pause(s)" if p["pauses_s"] else "") + (f", {p['sighs']} deep breath(s)" if p["sighs"] else "")
            print(f"BREATHING PATTERN: {p['level']}  (breath-to-breath variation {p['variability']:.0%}, {p['breaths']} breaths{extra}; from {p['from']})" + ("  [LOW CONFIDENCE]" if p.get("confidence") == "low" else ""))
        elif p:
            print("No breathing pattern.")
        for w in (p or {}).get("warnings", []):
            print("  ! " + w)
    h = res.get("hrv")
    if h:
        print()
        if h.get("beats"):
            print(f"  beats found {h['beats']}, clean intervals {h['clean_fraction']:.0%}, beat-by-beat HR {h.get('beat_hr_bpm')} bpm")
        if h.get("level") in ("rough", "low confidence"):
            r = h["range_ms"]
            print(("HRV (LOW CONFIDENCE): " if h["level"] == "low confidence" else "HRV (rough estimate): ") + f"SDNN {h['sdnn_ms']:.0f} ms (likely {r['sdnn'][0]:.0f}-{r['sdnn'][1]:.0f}), {h['band']}; "
                  f"RMSSD {h['rmssd_ms']:.0f} ms (likely {r['rmssd'][0]:.0f}-{r['rmssd'][1]:.0f})")
            print(f"  before removing timing noise: SDNN {h['uncorrected']['sdnn_ms']:.0f}, RMSSD {h['uncorrected']['rmssd_ms']:.0f} ms")
        elif h.get("level"):
            print(f"HRV: RMSSD {h['rmssd_ms']} ms, SDNN {h['sdnn_ms']} ms  ({h['level']})")
        else:
            rej = h.get("rejected")
            print("No reliable HRV." + (f" (rejected values: RMSSD {rej['rmssd_ms']} ms, SDNN {rej['sdnn_ms']} ms)" if rej else ""))
        for w in h["warnings"]:
            print("  ! " + w)
        print("  " + h["note"])
    r = res.get("rhythm")
    if r:
        print()
        if r.get("nrmssd") is not None:
            print(f"  intervals {r['intervals']}, detection slips {r['slips']}, beat-to-beat change {r['nrmssd']:.0%} of a beat "
                  f"(before removing timing noise {r['nrmssd_uncorrected']:.0%})")
        print(f"PULSE RHYTHM: {r['level']}" + ("  [LOW CONFIDENCE]" if r.get("confidence") == "low" else "") if r["level"] else "No pulse rhythm reading.")
        for w in r["warnings"]:
            print("  ! " + w)
        print("  " + r["note"])
    ps = res.get("pulse_strength")
    if ps:
        print()
        print(f"PULSE STRENGTH: {ps['pct']:.3f}% of skin brightness  (windows {ps['windows']}, spread {ps['spread']:.0%})" + ("  [LOW CONFIDENCE]" if ps.get("confidence") == "low" else "")
              if ps["pct"] is not None else "No pulse strength reading.")
        for w in ps["warnings"]:
            print("  ! " + w)
        print("  " + ps["note"])
    e = res.get("eyes")
    if e:
        print()
        if e["state"]:
            print(f"EYES: {e['state']}  (blinks {e['blinks_per_min']}/min, {e['rate_band']}; eyes mostly closed "
                  f"{e['perclos']:.0%} of the time; longest closure {e['longest_closure_s']} s)" + ("  [LOW CONFIDENCE]" if e.get("confidence") == "low" else ""))
        else:
            print("No eye reading.")
        for w in e["warnings"]:
            print("  ! " + w)
        print("  " + e["note"])
    o = res.get("spo2")
    if o:
        print()
        if o["spo2_pct"] is not None:
            print(f"SpO2: {o['spo2_pct']}%  (ratio {o['ratio_rb']}, windows {o.get('windows')})")
        elif o["ratio_rb"] is not None:
            print(f"SpO2 INDEX: red/blue {o['ratio_rb']}, red/green {o['ratio_rg']}  (windows {o.get('windows')}, spread {o['spread']:.0%}) - not a percentage")
        else:
            print("No reliable SpO2 index." + (f" (usable windows {o['windows']})" if o.get("windows") else ""))
        for w in o["warnings"]:
            print("  ! " + w)
        print("  " + o["note"])
    s = res.get("stress")
    if s:
        print()
        print(f"STRESS: {s['level']}  ({'; '.join(s['reasons'])}; based on {', '.join(s['based_on'])})" if s["level"] else "No stress indicator.")
        for w in s["warnings"]:
            print("  ! " + w)
        print("  " + s["note"])
    print(res["disclaimer"])


if __name__ == "__main__":
    sys.exit(main())
