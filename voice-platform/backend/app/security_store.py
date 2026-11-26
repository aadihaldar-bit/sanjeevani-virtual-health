"""Persist per-call jailbreak/prompt-injection guard events on calls.security_json."""
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def build_security_block(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "guard_events": events,
        "max_threat_severity": max((int(e.get("threat_severity", 0)) for e in events), default=0),
        "jailbreak_attempts": sum(1 for e in events if e.get("is_jailbreak")),
        "attack_types_seen": sorted({e.get("attack_type", "benign") for e in events
                                     if e.get("attack_type", "benign") != "benign"}),
        "turns_checked": len(events),
    }


def merge_event(block: Optional[Dict[str, Any]], event: Dict[str, Any]) -> Dict[str, Any]:
    """Add (or replace, by turn number) one event and recompute the aggregates."""
    events = [e for e in (block or {}).get("guard_events", []) if e.get("turn") != event.get("turn")]
    events.append(event)
    events.sort(key=lambda e: e.get("turn", 0))
    return build_security_block(events)


async def record_guard_event(call_id: Optional[Any], turn: int, guard_result: Dict[str, Any]) -> None:
    """Fire-and-forget safe: never raises into the call path."""
    if not call_id or not guard_result:
        return
    try:
        cid = int(call_id)
    except (TypeError, ValueError):
        return
    try:
        from .database import async_session
        from .models import Call
        from .websocket_manager import dashboard_manager

        event = {
            "turn": turn,
            "is_jailbreak": bool(guard_result.get("is_jailbreak")),
            "jailbreak_probability": guard_result.get("jailbreak_probability", 0.0),
            "attack_type": guard_result.get("attack_type", "benign"),
            "attack_type_confidence": guard_result.get("attack_type_confidence", 0.0),
            "threat_severity": int(guard_result.get("threat_severity", 0)),
            "guard_engine": guard_result.get("guard_engine", "unknown"),
            "source": "live",
        }
        async with async_session() as db:
            call = await db.get(Call, cid)
            if not call:
                return
            call.security_json = merge_event(call.security_json, event)
            await db.commit()
            if event["threat_severity"] >= 1 or event["is_jailbreak"]:
                await dashboard_manager.broadcast({"type": "call_updated", "call": call.to_dict()})
    except Exception as exc:
        logger.warning(f"[SecurityStore] could not record guard event for call {call_id}: {exc}")
