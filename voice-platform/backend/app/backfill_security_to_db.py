"""
Copy call_kpis.security from historical call JSONs in GCS into calls.security_json,
so the main dashboard shows guard results for old calls. Real stored classifications only.

Usage (inside backend container):
    python -m app.backfill_security_to_db --dry-run
    python -m app.backfill_security_to_db
"""
import argparse
import asyncio
import json
import logging

from .backfill_call_kpis import BUCKET_NAME, get_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("backfill_security_db")


async def main(dry_run: bool):
    from .database import async_session
    from .models import Call

    bucket = get_client().bucket(BUCKET_NAME)
    counts = {"updated": 0, "would_update": 0, "already_set": 0, "no_security": 0, "no_db_call": 0, "bad_id": 0}
    for blob in bucket.list_blobs(prefix="calls/"):
        if not blob.name.endswith(".json"):
            continue
        data = json.loads(blob.download_as_text())
        sec = (data.get("call_kpis") or {}).get("security")
        if not sec or not sec.get("guard_events"):
            counts["no_security"] += 1
            continue
        try:
            call_id = int(data.get("call_id") or data.get("id"))
        except (TypeError, ValueError):
            counts["bad_id"] += 1
            continue
        async with async_session() as db:
            call = await db.get(Call, call_id)
            if not call:
                counts["no_db_call"] += 1
                continue
            if call.security_json and call.security_json.get("guard_events"):
                counts["already_set"] += 1
                continue
            if dry_run:
                counts["would_update"] += 1
                continue
            call.security_json = sec
            await db.commit()
            counts["updated"] += 1
    logger.info(f"Result: {counts}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    asyncio.run(main(ap.parse_args().dry_run))
