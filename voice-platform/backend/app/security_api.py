"""Aggregated jailbreak / prompt-injection guard stats for the dashboard."""
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Dict

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from .database import get_db
from .models import Call

router = APIRouter(prefix="/api/security", tags=["security"])


@router.get("/summary")
async def security_summary(days: int = Query(30, ge=1, le=365), db: AsyncSession = Depends(get_db)) -> Dict[str, Any]:
    since = datetime.utcnow() - timedelta(days=days)
    total = (await db.execute(select(func.count(Call.id)).where(Call.started_at >= since))).scalar() or 0
    rows = (await db.execute(
        select(Call).where(Call.started_at >= since, Call.security_json.isnot(None))
        .order_by(Call.started_at.desc())
    )).scalars().all()
    rows = [c for c in rows if isinstance(c.security_json, dict) and c.security_json.get("guard_events")]

    severity, attacks = Counter(), Counter()
    turns = jailbreaks = flagged = 0
    recent = []
    for call in rows:
        sec = call.security_json or {}
        events = sec.get("guard_events", [])
        turns += len(events)
        jailbreaks += int(sec.get("jailbreak_attempts", 0))
        for e in events:
            severity[int(e.get("threat_severity", 0))] += 1
            if e.get("attack_type", "benign") != "benign":
                attacks[e["attack_type"]] += 1
        if int(sec.get("max_threat_severity", 0)) >= 1 or sec.get("jailbreak_attempts"):
            flagged += 1
            if len(recent) < 5:
                recent.append({
                    "call_id": call.id,
                    "caller_number": call.caller_number,
                    "started_at": call.started_at.isoformat() if call.started_at else None,
                    "max_threat_severity": sec.get("max_threat_severity", 0),
                    "attack_types_seen": sec.get("attack_types_seen", []),
                })

    return {
        "days": days,
        "calls_total": total,
        "calls_checked": len(rows),
        "turns_checked": turns,
        "flagged_calls": flagged,
        "jailbreak_attempts": jailbreaks,
        "severity_counts": {str(level): severity.get(level, 0) for level in range(4)},
        "attack_types": dict(attacks),
        "recent_flagged": recent,
    }
