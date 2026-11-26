"""
Backfill call_kpis.security for historical calls in GCS.

Every caller turn in each stored transcript is classified by the real guard
(JevDecisionEngine._guard_via_groq) - nothing is synthesized. A call is only
written if every user turn was classified; otherwise it is left untouched.

Usage (inside backend container):
    python -m app.backfill_security_events --dry-run
    python -m app.backfill_security_events [--limit N]
"""
import argparse
import asyncio
import json
import logging

from .backfill_call_kpis import BUCKET_NAME, get_client
from .jev_client import JevDecisionEngine
from .security_store import build_security_block

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("backfill_security")

CONCURRENCY = 3


def _user_turns(transcript):
    return [i for i, t in enumerate(transcript) if isinstance(t, dict) and t.get("role") == "user" and (t.get("text") or "").strip()]


def _context(transcript, idx):
    ctx = []
    for t in transcript[max(0, idx - 4):idx]:
        if not isinstance(t, dict):
            continue
        role = "user" if t.get("role") == "user" else "assistant"
        ctx.append({"role": role, "content": t.get("text", "")})
    return ctx


async def _classify(engine, sem, transcript, idx):
    async with sem:
        for attempt in range(3):
            res = await engine._guard_via_groq(transcript[idx]["text"], _context(transcript, idx) or None)
            if not res.is_fallback and res.guard_engine != "llm-judge-only":
                return res
            await asyncio.sleep(6 * (attempt + 1))
    return None


async def process_blob(engine, sem, blob, dry_run):
    data = json.loads(blob.download_as_text())
    existing = (data.get("call_kpis") or {}).get("security")
    if existing and not any(e.get("guard_engine") in ("groq", "llm-judge-only") for e in existing.get("guard_events", [])):
        return "skipped_has_security", 0
    transcript = data.get("transcript") or []
    idxs = _user_turns(transcript)
    if not idxs:
        return "skipped_no_user_turns", 0
    if dry_run:
        return "would_classify", len(idxs)

    results = await asyncio.gather(*[_classify(engine, sem, transcript, i) for i in idxs])
    if any(r is None for r in results):
        return "incomplete_left_untouched", len(idxs)

    events = []
    for n, r in enumerate(results, start=1):
        events.append({
            "turn": n,
            "is_jailbreak": r.is_jailbreak,
            "jailbreak_probability": r.jailbreak_probability,
            "attack_type": r.attack_type,
            "attack_type_confidence": r.attack_type_confidence,
            "threat_severity": r.threat_severity,
            "guard_engine": r.guard_engine,
            "source": "backfill",
        })
    kpis = data.get("call_kpis") or {}
    kpis["security"] = build_security_block(events)
    data["call_kpis"] = kpis
    blob.upload_from_string(json.dumps(data, indent=2, ensure_ascii=False), content_type="application/json")
    return "updated", len(idxs)


async def main(dry_run, limit):
    engine = JevDecisionEngine(api_key="")
    if not engine.guard_available:
        raise SystemExit("Guard not available (no GROQ_API_KEY)")
    bucket = get_client().bucket(BUCKET_NAME)
    blobs = [b for b in bucket.list_blobs(prefix="calls/") if b.name.endswith(".json")]
    if limit:
        blobs = blobs[:limit]
    logger.info(f"{len(blobs)} call blobs, dry_run={dry_run}")

    sem = asyncio.Semaphore(CONCURRENCY)
    counts, turns = {}, 0
    for b in blobs:
        try:
            status, n = await process_blob(engine, sem, b, dry_run)
        except Exception as exc:
            status, n = "error", 0
            logger.error(f"{b.name}: {exc}")
        counts[status] = counts.get(status, 0) + 1
        if status in ("updated", "would_classify"):
            turns += n
    logger.info(f"Result: {counts}; user turns {'to classify' if dry_run else 'classified'}: {turns}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    asyncio.run(main(args.dry_run, args.limit))
