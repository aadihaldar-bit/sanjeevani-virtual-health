"""Turn a webcam session or a video file into a trace: per-frame time, mean skin RGB, nose position, brightness."""
from __future__ import annotations

import queue
import threading
import time

import cv2
import numpy as np

from .face import PATCH_LANDMARKS, FaceSampler
from .resp import ChestTracker

ARRAYS = ("t", "rgb", "nose", "brightness", "face_y", "chest_dy", "patches", "mouth", "face_size", "eye")


def _empty():
    return {k: [] for k in ARRAYS}


def _add(tr, t, frame, s, chest: ChestTracker):
    tr["t"].append(t)
    tr["rgb"].append(s["rgb"] if s else [np.nan] * 3)
    tr["nose"].append(s["nose"] if s else (np.nan, np.nan))
    tr["brightness"].append(float(frame.mean()))
    tr["face_y"].append(s["face_y"] if s else np.nan)
    tr["chest_dy"].append(chest.update(frame, s))
    tr["patches"].append(s["patches"] if s else np.full((len(PATCH_LANDMARKS), 3), np.nan))
    tr["mouth"].append(s["mouth"] if s else np.nan)
    tr["face_size"].append(s["face_size"] if s else np.nan)
    tr["eye"].append(s["eye"] if s else np.nan)


def _finish(tr, meta):
    out = {k: np.asarray(v, float) for k, v in tr.items()}
    out["t"] = out["t"] - out["t"][0] if len(out["t"]) else out["t"]
    out["meta"] = meta
    return out


def save_trace(path: str, tr: dict) -> None:
    import json
    np.savez(path, meta=json.dumps(tr["meta"]), **{k: tr[k] for k in ARRAYS if k in tr})


def load_trace(path: str) -> dict:
    import json
    z = np.load(path, allow_pickle=False)
    tr = {k: z[k] for k in ARRAYS if k in z.files}          # traces saved before breathing was added lack face_y/chest_dy
    tr["meta"] = json.loads(str(z["meta"]))
    return tr


def trace_from_video(path: str, sampler: FaceSampler | None = None, max_seconds: float | None = None) -> dict:
    sampler = sampler or FaceSampler()
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise FileNotFoundError(f"cannot open video: {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    tr, i, chest = _empty(), 0, ChestTracker()
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        t = i / fps
        if max_seconds and t > max_seconds:
            break
        _add(tr, t, frame, sampler.sample(frame), chest)
        i += 1
    cap.release()
    if i < 2:
        raise ValueError(f"no frames read from {path}")
    return _finish(tr, {"source": path, "nominal_fps": fps, "frames": i, "chest_visible": chest.visible})


MIN_FACE_BRIGHTNESS, MAX_FACE_BRIGHTNESS = 70, 215


def _hint(s, bright):
    if s is None:
        if bright < 60:
            return "Too dark - put a lamp in front of you, facing you", (0, 165, 255)
        return "No face found - face the camera, in even light", (0, 0, 255)
    fb = s["face_brightness"]
    if fb < MIN_FACE_BRIGHTNESS:
        return f"Face too dark ({fb:.0f}) - need a lamp in front of you, facing you", (0, 165, 255)
    if fb > MAX_FACE_BRIGHTNESS:
        return "Too bright - avoid glare on the forehead", (0, 165, 255)
    return "Face found - hold still, press SPACE to start", (0, 200, 0)


def _try_lock_exposure(cap, read_brightness):
    """Freeze auto exposure/white balance so the camera's own corrections are not read as a pulse. Many webcams ignore this,
    and a lock that changes the picture is undone. Returns True if the lock held."""
    before = read_brightness()
    exposure, wb = cap.get(cv2.CAP_PROP_EXPOSURE), cap.get(cv2.CAP_PROP_WB_TEMPERATURE)
    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
    cap.set(cv2.CAP_PROP_EXPOSURE, exposure)
    cap.set(cv2.CAP_PROP_AUTO_WB, 0)
    cap.set(cv2.CAP_PROP_WB_TEMPERATURE, wb)
    time.sleep(0.6)
    after = read_brightness()
    if before > 0 and abs(after - before) / before > 0.15:
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.75)
        cap.set(cv2.CAP_PROP_AUTO_WB, 1)
        return False
    return True


def _draw(frame, s, text, color, extra=""):
    if s:
        for poly in s["polys"]:
            cv2.polylines(frame, [poly], True, (0, 255, 0), 1)
    cv2.putText(frame, text, (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
    if extra:
        cv2.putText(frame, extra, (10, 56), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)


def camera_check(camera: int = 0, seconds: float = 6.0) -> list[dict]:
    """Grab frames with no face detection at all, for each pixel format, to see what the camera itself can deliver."""
    rows = []
    for name in (None, "MJPG", "YUY2"):
        cap = cv2.VideoCapture(camera, cv2.CAP_DSHOW)
        if not cap.isOpened():
            raise RuntimeError("cannot open the webcam (is another app using it?)")
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        if name:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*name))
        cap.set(cv2.CAP_PROP_FPS, 30)
        t_end, times, bright = time.perf_counter() + 1.0, [], []
        while time.perf_counter() < t_end:          # let the camera settle
            cap.read()
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < seconds:
            ok, f = cap.read()
            if ok:
                times.append(time.perf_counter() - t0)
                bright.append(float(f.mean()))
        got = int(cap.get(cv2.CAP_PROP_FOURCC))
        cap.release()
        dt = np.diff(times) if len(times) > 2 else np.array([1.0])
        rows.append({"requested": name or "default", "got": "".join(chr((got >> (8 * i)) & 0xFF) for i in range(4)).strip("\x00 "),
                     "fps": round(len(times) / seconds, 1), "median_ms": round(float(np.median(dt)) * 1e3, 1),
                     "p95_ms": round(float(np.percentile(dt, 95)) * 1e3, 1), "max_ms": round(float(dt.max()) * 1e3),
                     "brightness": round(float(np.mean(bright)), 1) if bright else None,
                     "brightness_range": round(float(np.ptp(bright)), 1) if bright else None})
    return rows


def record_trace(seconds: float = 40.0, camera: int = 0, sampler: FaceSampler | None = None, countdown: int = 3) -> dict | None:
    """Preview with a lighting/face check, then record. Press SPACE to start, Q to quit. Returns None if cancelled."""
    sampler = sampler or FaceSampler()
    cap = cv2.VideoCapture(camera, cv2.CAP_DSHOW)
    if not cap.isOpened():
        raise RuntimeError("cannot open the webcam (is another app using it?)")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS, 30)
    meta = {"source": f"webcam:{camera}", "width": cap.get(cv2.CAP_PROP_FRAME_WIDTH), "height": cap.get(cv2.CAP_PROP_FRAME_HEIGHT)}

    frames: "queue.Queue" = queue.Queue(maxsize=400)
    stop, dropped = threading.Event(), [0]

    def grab():
        t0 = time.perf_counter()
        while not stop.is_set():
            ok, f = cap.read()
            if not ok:
                continue
            try:
                frames.put_nowait((time.perf_counter() - t0, f))
            except queue.Full:
                dropped[0] += 1

    th = threading.Thread(target=grab, daemon=True)
    th.start()
    win = "vitals - SPACE start, Q quit"
    tr, state, started_at, countdown_end = _empty(), "preview", None, None
    chest = ChestTracker()
    try:
        while True:
            try:
                t, frame = frames.get(timeout=1.0)
            except queue.Empty:
                raise RuntimeError("the webcam stopped delivering frames")
            s = sampler.sample(frame)
            bright = float(frame.mean())
            shown = frame.copy()
            if state == "preview":
                text, color = _hint(s, bright)
                _draw(shown, s, text, color, f"brightness {bright:.0f}")
            elif state == "countdown":
                left = countdown_end - time.perf_counter()
                if left <= 0:
                    state, started_at = "record", t
                _draw(shown, s, f"Starting in {max(left, 0):.0f}...", (255, 255, 255))
            else:
                elapsed = t - started_at
                _add(tr, t, frame, s, chest)
                _draw(shown, s, f"Recording {elapsed:4.1f}/{seconds:.0f}s - keep still, don't talk",
                      (0, 200, 0) if s else (0, 0, 255))
                if elapsed >= seconds:
                    break
            cv2.imshow(win, shown)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                return None
            if key == ord(" ") and state == "preview":
                state, countdown_end = "countdown", time.perf_counter() + countdown
                latest = [bright]

                def read_brightness():
                    # Main thread is not draining the queue here, so take the newest frames the grabber has produced.
                    end = time.perf_counter() + 0.4
                    vals = []
                    while time.perf_counter() < end:
                        try:
                            vals.append(float(frames.get(timeout=0.2)[1].mean()))
                        except queue.Empty:
                            break
                    return float(np.mean(vals)) if vals else latest[0]

                meta["exposure_locked"] = _try_lock_exposure(cap, read_brightness)
    finally:
        stop.set()
        th.join(timeout=2)
        cap.release()
        cv2.destroyAllWindows()
    meta["dropped_frames"] = dropped[0]
    meta["chest_visible"] = chest.visible
    return _finish(tr, meta)
