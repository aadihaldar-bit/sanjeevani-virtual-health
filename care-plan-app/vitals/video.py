"""Browser recordings arrive as WebM or MP4 with an uneven frame rate (and often no duration in the header), but the
analysis reads frame i as time i / nominal fps. Such files are re-timed here to a steady 30 fps in high-quality H.264:
for each 1/30 s tick, the frame on screen at that moment. Phone and camera files that are already steady are used as
they are, so they are not compressed twice."""
from __future__ import annotations

import av
import cv2
import numpy as np

FPS = 30
MAX_SIDE = 1280                   # the face crop is 72x72; more pixels only slow face tracking down
MIN_SECONDS, MAX_SECONDS = 20.0, 180.0
STEADY_CV = 0.10                  # frame-gap variation below this counts as a steady frame rate


class VideoError(ValueError):
    """A file the analysis cannot use, with a message meant for the person who sent it."""


def probe(path: str) -> dict:
    """Duration, real frame rate and whether the analysis can read the file as it is. Reads packets, not pixels."""
    try:
        c = av.open(path)
    except (av.error.FFmpegError, OSError) as e:
        raise VideoError("this file could not be read as a video") from e
    with c:
        if not c.streams.video:
            raise VideoError("the file has no video in it")
        s = c.streams.video[0]
        tb = s.time_base
        t = np.sort([float(p.pts * tb) for p in c.demux(s) if p.pts is not None and p.size])
        fmt = c.format.name
    if len(t) < 2 or t[-1] <= t[0]:
        raise VideoError("the video has no frames")
    dur = float(t[-1] - t[0])
    fps = (len(t) - 1) / dur
    dt = np.diff(t)
    cap = cv2.VideoCapture(path)
    nominal = cap.get(cv2.CAP_PROP_FPS) or 0.0
    cap.release()
    steady = (float(np.std(dt) / np.mean(dt)) < STEADY_CV and nominal > 0 and abs(nominal - fps) / fps < 0.03
              and not any(k in fmt for k in ("webm", "matroska")))
    return {"seconds": round(dur, 1), "fps": round(fps, 1), "nominal_fps": round(nominal, 1), "steady": steady,
            "format": fmt}


def check_length(info: dict):
    if info["seconds"] < MIN_SECONDS:
        raise VideoError(f"the video is {info['seconds']:.0f} s long; it needs at least {MIN_SECONDS:.0f} s "
                         "(about a minute gives the best readings)")
    if info["seconds"] > MAX_SECONDS:
        raise VideoError(f"the video is {info['seconds']:.0f} s long; send at most {MAX_SECONDS / 60:.0f} minutes")


def retime(src: str, dst: str) -> int:
    """Write src to dst as steady 30 fps H.264, turned upright. Returns the number of frames written."""
    with av.open(src) as ic, av.open(dst, "w") as oc:
        s = ic.streams.video[0]
        s.thread_type = "AUTO"
        out, last, t0, k = None, None, None, 0

        def emit(img):
            nonlocal out
            if out is None:
                h, w = img.shape[:2]
                out = oc.add_stream("libx264", rate=FPS)
                out.width, out.height, out.pix_fmt = w, h, "yuv420p"
                out.options = {"crf": "14", "preset": "veryfast"}
            for p in out.encode(av.VideoFrame.from_ndarray(img, format="rgb24")):
                oc.mux(p)

        for f in ic.decode(s):
            if f.time is None:
                continue
            img = f.to_ndarray(format="rgb24")
            if getattr(f, "rotation", 0):
                img = np.rot90(img, f.rotation // 90)
            scale = MAX_SIDE / max(img.shape[:2])
            if scale < 1:
                img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            img = np.ascontiguousarray(img[: img.shape[0] // 2 * 2, : img.shape[1] // 2 * 2])
            if t0 is None:
                t0 = f.time
            while last is not None and t0 + k / FPS < f.time:    # ticks before this frame show the previous one
                emit(last)
                k += 1
            last = img
        if last is not None:
            emit(last)
            k += 1
        if out is not None:
            for p in out.encode():
                oc.mux(p)
    return k
