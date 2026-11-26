"""
Backfill per-call KPIs for existing calls in GCS bucket.
Reads each call JSON, computes estimated KPIs from transcript/duration/ticket data,
adds a `call_kpis` field, and re-uploads to the same blob path.

Usage (inside backend container):
    python -m app.backfill_call_kpis
"""

import os
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from google.auth import load_credentials_from_file
from google.cloud import storage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BUCKET_NAME = os.getenv("GCS_LOG_BUCKET", "stld-bkt-data-prod-voicebot-log-uscentral01-001").strip()
CREDENTIAL_PATHS = [
    "/app/secrets/gcs-credentials.json",
    "/opt/stl-voicebot/secrets/gcs-credentials.json",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "secrets", "gcs-credentials.json"),
    "secrets/gcs-credentials.json",
]


def get_client():
    cred_file = None
    for p in CREDENTIAL_PATHS:
        if os.path.exists(p):
            cred_file = p
            break
    if cred_file:
        creds, _ = load_credentials_from_file(cred_file)
        return storage.Client(project="voicebot-support", credentials=creds)
    return storage.Client(project="voicebot-support")


def estimate_call_kpis(call_data):
    transcript = call_data.get("transcript") or []
    turns = len([t for t in transcript if isinstance(t, dict) and t.get("role") == "user"])
    agent_turns = len([t for t in transcript if isinstance(t, dict) and t.get("role") in ("agent", "assistant")])
    total_chars = sum(len(t.get("text", "")) for t in transcript if isinstance(t, dict) and t.get("role") in ("agent", "assistant"))
    duration = call_data.get("duration_seconds") or 60.0
    try:
        duration = float(duration)
    except (ValueError, TypeError):
        duration = 60.0

    prompt_tokens = turns * 350
    completion_tokens = agent_turns * 120
    total_tokens = prompt_tokens + completion_tokens

    llm_cost = total_tokens / 1_000_000 * 0.59
    stt_cost = duration * 0.00005
    tts_cost = total_chars / 1000.0 * 0.015
    total_cost = llm_cost + stt_cost + tts_cost

    has_ticket = bool((call_data.get("ticket") or {}).get("created"))
    avg_turn_latency = 1657.0 if not has_ticket else 2800.0

    return {
        "turn_count": turns,
        "interruption_count": 0,
        "stt_latency_ms": {"avg": 210.0, "median": 200.0},
        "llm_route_latency_ms": {"avg": avg_turn_latency, "median": round(avg_turn_latency * 0.85, 1)},
        "tts_ttfb_ms": {"avg": 180.0, "median": 160.0},
        "total_turn_latency_ms": {"avg": round(avg_turn_latency + 390.0, 1), "median": round((avg_turn_latency + 390.0) * 0.9, 1)},
        "token_usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        },
        "estimated_cost_usd": {
            "llm": round(llm_cost, 6),
            "stt": round(stt_cost, 6),
            "tts": round(tts_cost, 6),
            "total": round(total_cost, 6),
        },
        "source": "estimated_backfill",
    }


def backfill_blob(blob):
    try:
        data = json.loads(blob.download_as_text())
        if data.get("call_kpis"):
            return {"blob": blob.name, "status": "skipped", "reason": "already has call_kpis"}

        data["call_kpis"] = estimate_call_kpis(data)
        blob.upload_from_string(
            json.dumps(data, indent=2, ensure_ascii=False),
            content_type="application/json",
        )
        return {"blob": blob.name, "status": "updated", "turns": data["call_kpis"]["turn_count"]}
    except Exception as exc:
        return {"blob": blob.name, "status": "error", "error": str(exc)}


def main():
    client = get_client()
    bucket = client.bucket(BUCKET_NAME)

    call_blobs = [b for b in bucket.list_blobs(prefix="calls/") if b.name.endswith(".json")]
    logger.info(f"Found {len(call_blobs)} call blobs in gs://{BUCKET_NAME}/calls/")

    updated = 0
    skipped = 0
    errors = 0

    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = {executor.submit(backfill_blob, b): b.name for b in call_blobs}
        for fut in as_completed(futures):
            result = fut.result()
            if result["status"] == "updated":
                updated += 1
                logger.info(f"  Updated: {result['blob']} ({result['turns']} turns)")
            elif result["status"] == "skipped":
                skipped += 1
            else:
                errors += 1
                logger.error(f"  Error: {result['blob']}: {result.get('error')}")

    logger.info(f"\nBackfill complete: {updated} updated, {skipped} skipped, {errors} errors (out of {len(call_blobs)} total)")


if __name__ == "__main__":
    main()
