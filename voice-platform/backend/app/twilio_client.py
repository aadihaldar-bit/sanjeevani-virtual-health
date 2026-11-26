"""
Twilio Telephony Service Segment
Implements BaseTelephonyService conforming to Single Responsibility & Dependency Inversion.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
from xml.sax.saxutils import escape as xml_escape
from sqlalchemy import select

from .interfaces import BaseTelephonyService
from .config import TelephonySettings, get_config
from .database import async_session
from .models import Call, CallStatus, Event
from .event_logger import event_logger

logger = logging.getLogger(__name__)

TWILIO_ACTIVE_STATUSES = ("queued", "ringing", "in-progress")
TWILIO_TERMINAL_STATUSES = ("completed", "busy", "no-answer", "failed", "canceled")
TWILIO_FAILED_STATUSES = {"busy", "no-answer", "failed", "canceled"}


class TwilioSyncClient(BaseTelephonyService):
    """Encapsulates all Twilio API calls, call reconciliation, SMS, and transfers."""

    def __init__(self, settings: Optional[TelephonySettings] = None):
        self.settings = settings or get_config().telephony
        self.account_sid = self.settings.account_sid
        self.auth_token = self.settings.auth_token
        self.phone_number = self.settings.phone_number
        self._client = None
        self._disabled_due_to_auth_error = False

    def is_configured(self) -> bool:
        return bool(self.account_sid and self.auth_token)

    def _get_client(self):
        if not self._client:
            from twilio.rest import Client
            self._client = Client(self.account_sid, self.auth_token)
        return self._client

    @property
    def twilio_client(self):
        """Expose underlying Twilio REST client for direct SDK calls."""
        return self._get_client()

    @staticmethod
    def _to_naive_utc(value):
        if value is None:
            return None
        if getattr(value, "tzinfo", None):
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def _normalize_call(self, call) -> dict:
        status = (call.status or "").lower()
        direction = getattr(call, "direction", None) or ""
        from_number = getattr(call, "from_", None)
        to_number = getattr(call, "to", None)
        is_outbound = direction.startswith("outbound")

        customer_number = to_number if is_outbound else from_number
        platform_number = from_number if is_outbound else to_number
        if not platform_number:
            platform_number = self.phone_number

        duration = getattr(call, "duration", None)
        try:
            duration = float(duration) if duration is not None else None
        except (TypeError, ValueError):
            duration = None

        return {
            "call_sid": call.sid,
            "status": status,
            "from": from_number,
            "to": to_number,
            "customer_number": customer_number,
            "platform_number": platform_number,
            "direction": direction,
            "date_created": self._to_naive_utc(getattr(call, "date_created", None)),
            "start_time": self._to_naive_utc(getattr(call, "start_time", None)),
            "end_time": self._to_naive_utc(getattr(call, "end_time", None)),
            "duration": duration,
        }

    def _to_json_compatible(self, data: dict) -> dict:
        if not isinstance(data, dict):
            return data
        result = {}
        for k, v in data.items():
            if isinstance(v, datetime):
                result[k] = v.isoformat()
            elif isinstance(v, dict):
                result[k] = self._to_json_compatible(v)
            elif isinstance(v, list):
                result[k] = [
                    self._to_json_compatible(x) if isinstance(x, dict) else (x.isoformat() if isinstance(x, datetime) else x)
                    for x in v
                ]
            else:
                result[k] = v
        return result

    async def list_active_calls(self) -> List[Dict[str, Any]]:
        if not self.is_configured():
            return []

        def _fetch():
            client = self._get_client()
            calls = []
            for status in TWILIO_ACTIVE_STATUSES:
                calls.extend(client.calls.list(status=status, limit=50))
            return calls

        results = await asyncio.to_thread(_fetch)
        return [self._normalize_call(call) for call in results]

    async def list_recent_calls(self, limit: int = 50) -> List[Dict[str, Any]]:
        if not self.is_configured():
            return []

        def _fetch():
            client = self._get_client()
            return client.calls.list(limit=limit)

        results = await asyncio.to_thread(_fetch)
        return [self._normalize_call(call) for call in results]

    async def sync_active_calls(self) -> Dict[str, Any]:
        stats = {"upserted": 0, "completed": 0, "active_count": 0, "errors": 0}
        if self._disabled_due_to_auth_error or not self.is_configured():
            return stats

        try:
            active_calls = await self.list_active_calls()
            active_sids = {call["call_sid"] for call in active_calls if call.get("call_sid")}

            async with async_session() as session:
                for item in active_calls:
                    result = await session.execute(
                        select(Call).where(Call.call_sid == item["call_sid"])
                    )
                    call = result.scalars().first()
                    timestamp = datetime.utcnow()
                    if not call:
                        continue

                    # Update existing call
                    call.live_status = item["status"]
                    call.last_event_at = timestamp
                    
                    if item["status"] in ["completed", "failed", "busy", "no-answer", "canceled"]:
                        call.status = CallStatus.COMPLETED
                        call.ended_at = timestamp
                        call.end_reason = f"twilio_sync_{item['status']}"
                    else:
                        call.ended_at = None
                        call.end_reason = None

                    call.caller_number = item.get("from") or call.caller_number
                    call.called_number = item.get("to") or call.called_number

                    session.add(Event(
                        call_id=call.id,
                        event_type="twilio_active_sync",
                        payload=self._to_json_compatible(item),
                    ))
                    stats["upserted"] += 1

                stale_threshold = datetime.utcnow() - timedelta(seconds=45)
                stale_result = await session.execute(
                    select(Call).where(
                        Call.status == CallStatus.ACTIVE,
                        Call.call_sid.is_not(None),
                        Call.source == "twilio",
                    )
                )
                for call in stale_result.scalars().all():
                    # Strictly guard against reconciling non-Twilio calls (e.g. Smartflo)
                    if not call.source or call.source.lower() != "twilio":
                        await event_logger.emit("reconciler.skipped", call_sid=call.call_sid, reason="non_twilio_source", source=call.source)
                        continue
                    sid_str = str(call.call_sid or "")
                    if not sid_str.startswith("CA") or sid_str.startswith("CAGE3"):
                        await event_logger.emit("reconciler.skipped", call_sid=call.call_sid, reason="smartflo_or_non_twilio_prefix", sid=sid_str)
                        continue
                    if call.call_sid in active_sids:
                        continue
                    if call.started_at and call.started_at > stale_threshold:
                        continue
                    call.status = CallStatus.COMPLETED
                    call.live_status = "completed"
                    call.source = "twilio"
                    call.ended_at = datetime.utcnow()
                    call.last_event_at = call.ended_at
                    if not call.end_reason:
                        call.end_reason = "twilio_sync_reconciled"
                    if call.started_at and call.duration_seconds is None:
                        call.duration_seconds = (call.ended_at - call.started_at).total_seconds()
                    session.add(Event(
                        call_id=call.id,
                        event_type="twilio_active_reconciled",
                        payload={"call_sid": call.call_sid},
                    ))
                    await event_logger.emit(
                        "reconciler.completed",
                        call_sid=call.call_sid,
                        duration_seconds=call.duration_seconds,
                        end_reason=call.end_reason
                    )
                    stats["completed"] += 1

                await session.commit()
                stats["active_count"] = len(active_sids)

        except Exception as exc:
            if "401" in str(exc) or "20003" in str(exc) or "Authenticate" in str(exc):
                logger.warning("Twilio API returned 401 Unauthorized. Pausing Twilio background poll.")
                self._disabled_due_to_auth_error = True
            else:
                logger.error("Twilio active call sync failed: %s", exc, exc_info=True)
            stats["errors"] += 1
            return stats

        return stats

    async def send_sms(self, to_number: str, body: str) -> Dict[str, Any]:
        if not self.is_configured():
            raise ValueError("Twilio credentials not configured")
            
        from_number = self.phone_number
        if not from_number:
            raise ValueError("TWILIO_PHONE_NUMBER not configured")

        def _send():
            client = self._get_client()
            return client.messages.create(
                body=body,
                from_=from_number,
                to=to_number
            )

        try:
            message = await asyncio.to_thread(_send)
            logger.info("Sent SMS via Twilio to %s: %s", to_number, message.sid)
            return {"sid": message.sid, "status": message.status}
        except Exception as exc:
            logger.error("Failed to send SMS to %s: %s", to_number, exc)
            raise

    async def transfer_call(self, call_sid: str, target_number: str) -> bool:
        if not self.is_configured():
            logger.error("Twilio credentials not configured for transfer")
            return False

        def _transfer():
            client = self._get_client()
            twilio_number = self.phone_number or "+13527253511"
            # Security: escape any value interpolated into TwiML — this is XML,
            # and unescaped content can inject sibling elements (e.g. <Redirect>)
            # that Twilio would parse and execute as further call-control.
            safe_caller_id = xml_escape(twilio_number)
            safe_target = xml_escape(target_number)
            twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>Transferring you to a live agent. Please hold.</Say>
    <Dial callerId="{safe_caller_id}">{safe_target}</Dial>
</Response>"""
            return client.calls(call_sid).update(twiml=twiml)

        try:
            await asyncio.to_thread(_transfer)
            logger.info("Call %s transferred to %s", call_sid, target_number)
            return True
        except Exception as exc:
            logger.error("Failed to transfer call %s: %s", call_sid, exc)
            return False

    async def end_call(self, call_sid: str) -> bool:
        if not self.is_configured():
            return False

        def _end():
            client = self._get_client()
            return client.calls(call_sid).update(status="completed")

        try:
            await asyncio.to_thread(_end)
            logger.info("Call %s ended via Twilio API", call_sid)
            return True
        except Exception as exc:
            logger.error("Failed to end call %s: %s", call_sid, exc)
            return False


# Global singleton
twilio_sync_client = TwilioSyncClient()
