"""Close calls that are still marked active but have gone quiet.

A browser tab closed mid-simulation, a dropped carrier stream or a crashed session leaves its call row active
forever, and the live roster then shows it with an ever-growing duration. A call counts as stale when nothing has
happened on it (no transcript message, no event) for longer than a threshold: shorter for web-simulator sessions,
longer for phone calls. The check is activity-based, never "older than N minutes", so a long live call is safe.
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, select

from .database import async_session
from .models import Call, CallStatus, TranscriptMessage

logger = logging.getLogger(__name__)

SIMULATOR_IDLE_MIN = float(os.getenv("STALE_SIMULATOR_CALL_MIN", "10"))
PHONE_IDLE_MIN = float(os.getenv("STALE_PHONE_CALL_MIN", "20"))
SWEEP_EVERY_SECS = int(os.getenv("STALE_CALL_SWEEP_SECS", "60"))


def _is_simulator(call: Call) -> bool:
    return (call.source or "") == "simulator" or (call.caller_number or "") == "Web Simulator"


async def reap_stale_calls(db, now: Optional[datetime] = None) -> int:
    """Mark quiet active calls completed. Returns how many were closed; the caller's session is committed."""
    now = now or datetime.utcnow()
    calls = (await db.execute(select(Call).where(Call.status == CallStatus.ACTIVE))).scalars().all()
    if not calls:
        return 0
    last_msg = dict((await db.execute(
        select(TranscriptMessage.call_id, func.max(TranscriptMessage.timestamp))
        .where(TranscriptMessage.call_id.in_([c.id for c in calls])).group_by(TranscriptMessage.call_id))).all())
    closed = 0
    for c in calls:
        activity = max([t for t in (last_msg.get(c.id), c.last_event_at, c.started_at) if t is not None], default=None)
        if activity is None:
            continue
        idle_limit = SIMULATOR_IDLE_MIN if _is_simulator(c) else PHONE_IDLE_MIN
        if now - activity < timedelta(minutes=idle_limit):
            continue
        c.status = CallStatus.COMPLETED
        c.live_status = "completed"
        c.ended_at = c.ended_at or activity
        if c.duration_seconds is None and c.started_at:
            c.duration_seconds = max(0.0, (activity - c.started_at).total_seconds())
        if not c.end_reason:
            c.end_reason = "auto_closed_inactive"
        closed += 1
    if closed:
        await db.commit()
        logger.info(f"[CallReaper] closed {closed} inactive call(s)")
    return closed


async def reaper_loop() -> None:
    """Background sweep so the database stays clean even when nobody has the console open."""
    while True:
        try:
            await asyncio.sleep(SWEEP_EVERY_SECS)
            async with async_session() as db:
                await reap_stale_calls(db)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"[CallReaper] sweep failed: {exc}")
