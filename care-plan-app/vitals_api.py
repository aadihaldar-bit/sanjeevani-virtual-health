"""Vitals check: a short face video in, wellness readings out (heart rate, breathing, HRV, rhythm, stress, eyes).

Runs on this server's CPU, one video at a time (it uses every core); later uploads wait in a queue and the page shows
their place. The video is deleted as soon as its analysis ends and the readings live only in memory for
RESULT_TTL_S, never in the database: they are estimates for one session, not medical records.

Its packages are in requirements.txt. If one is missing the rest of the app still runs and these endpoints answer 503
naming it."""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

router = APIRouter(prefix="/api/v1/vitals", tags=["Vitals check"])

MAX_BYTES = 400 * 1024 * 1024
RESULT_TTL_S = 30 * 60
STEP_NAMES = {"classical methods (face tracking)...": "Following the face and skin colour",
              "phone-video model (CPU)...": "Reading the pulse from the video"}

_pool = ThreadPoolExecutor(max_workers=1)
_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _engine():
    """The analysis modules, or a 503 naming what is missing."""
    try:
        from vitals.__main__ import run_analysis
        from vitals.present import present
        from vitals.video import VideoError, check_length, probe, retime
    except ImportError as e:
        raise HTTPException(503, f"vitals check is not installed on this server ({e.name}); "
                                 "pip install -r requirements.txt") from e
    return run_analysis, present, VideoError, check_length, probe, retime


def _forget_old():
    now = time.time()
    with _lock:
        for k in [k for k, j in _jobs.items() if j["status"] in ("done", "error") and now - j["finished"] > RESULT_TTL_S]:
            del _jobs[k]


def _run(job: dict, folder: str, path: str):
    run_analysis, present, VideoError, check_length, probe, retime = _engine()
    job["status"], job["started"] = "running", time.time()

    def step(text, *_):
        text = STEP_NAMES.get(text)
        if text and job["steps"][-1:] != [text]:
            job["steps"].append(text)

    try:
        job["steps"].append("Checking the video")
        info = probe(path)
        check_length(info)
        if not info["steady"]:                     # browser recordings: uneven frame timing, evened out first
            job["steps"].append("Evening out the frame timing")
            steady = os.path.join(folder, "steady.mp4")
            retime(path, steady)
            path = steady
        res = run_analysis(path, fast=True, log=step, use_context=False)
        job["result"], job["status"] = present(res, info), "done"
    except VideoError as e:
        job["status"], job["error"] = "error", str(e)
    except Exception:                              # never show a stack trace to the patient; keep it in the log
        traceback.print_exc()
        job["status"], job["error"] = "error", "the analysis failed on this video; try again with the face well lit and still"
    finally:
        job["finished"] = time.time()
        shutil.rmtree(folder, ignore_errors=True)


@router.post("/analyze")
async def analyze(video: UploadFile = File(...)):
    _engine()
    _forget_old()
    folder = tempfile.mkdtemp(prefix="vitals_")
    ext = os.path.splitext(video.filename or "")[1].lower()
    path = os.path.join(folder, "upload" + (ext if ext in (".mp4", ".mov", ".webm", ".mkv", ".m4v") else ".bin"))
    size = 0
    with open(path, "wb") as f:
        while chunk := await video.read(1 << 20):
            size += len(chunk)
            if size > MAX_BYTES:
                f.close()
                shutil.rmtree(folder, ignore_errors=True)
                raise HTTPException(413, f"the video is over {MAX_BYTES >> 20} MB")
            f.write(chunk)
    job = {"id": uuid.uuid4().hex, "status": "queued", "steps": [], "result": None, "error": None,
           "created": time.time(), "started": None, "finished": None}
    with _lock:
        _jobs[job["id"]] = job
    _pool.submit(_run, job, folder, path)
    return {"id": job["id"]}


@router.get("/jobs/{job_id}")
def job_status(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "this result has expired or never existed")
    ahead = sum(1 for j in list(_jobs.values()) if j["status"] in ("queued", "running") and j["created"] < job["created"])
    return {"status": job["status"], "steps": job["steps"], "queue_position": ahead if job["status"] == "queued" else 0,
            "elapsed_s": round(time.time() - job["started"], 1) if job["started"] else 0,
            "result": job["result"], "error": job["error"]}


@router.delete("/jobs/{job_id}")
def forget(job_id: str):
    with _lock:
        _jobs.pop(job_id, None)
    return {"ok": True}


@router.get("/status")
def status():
    try:
        _engine()
    except HTTPException as e:
        return {"available": False, "reason": e.detail}
    from vitals import phone_model
    m = phone_model.meta() if phone_model.available() else {}
    return {"available": True, "trained_on": m.get("trained_on"), "min_seconds": 20, "max_seconds": 180}
