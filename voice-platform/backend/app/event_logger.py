"""
Event Logger Module — Daily Structured NDJSON Event Logging Engine
Captures observable system, telephony, authentication, router, tool, and webhook events.
Appends JSON lines asynchronously to logs/events/YYYY-MM-DD.ndjson.
Includes midnight file rotation, configurable retention, and GCS bucket flush hooks.
Supports date-wise partitioning: events/dt=YYYY-MM-DD/YYYY-MM-DD.ndjson.
"""

import os
import json
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_GCS_BUCKET = "stld-bkt-data-prod-voicebot-log-uscentral01-001"


class EventLogger:
    """
    Central singleton event logging engine.
    Drains an in-memory queue to write append-only NDJSON daily logs on disk.
    Periodically or on-demand flushes logs to Google Cloud Storage with date partitioning.
    """

    def __init__(self, log_dir: Optional[str] = None):
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.log_dir = log_dir or os.getenv("EVENT_LOG_DIR", os.path.join(base_dir, "logs", "events"))
        self.bucket_name = os.getenv("GCS_LOG_BUCKET", DEFAULT_GCS_BUCKET).strip()
        self.gcs_prefix = os.getenv("GCS_LOG_PREFIX", "events").strip()
        self.flush_interval = int(os.getenv("GCS_FLUSH_INTERVAL_SECONDS", "3600"))
        self.project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT") or "voicebot-support"

        self.queue: asyncio.Queue = asyncio.Queue(maxsize=10000)
        self.worker_task: Optional[asyncio.Task] = None
        self.gcs_task: Optional[asyncio.Task] = None
        self.is_running = False

        self.retention_days = int(os.getenv("EVENT_LOG_RETENTION_DAYS", "30"))
        self.full_transcripts = os.getenv("EVENT_LOG_FULL_TRANSCRIPTS", "true").lower() == "true"
        self._last_cleanup_date: Optional[str] = None

        self.last_gcs_flush: Optional[str] = None
        self.last_gcs_status: Optional[str] = None
        self.last_gcs_error: Optional[str] = None

        os.makedirs(self.log_dir, exist_ok=True)

    def _get_current_log_path(self) -> str:
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return os.path.join(self.log_dir, f"{today_str}.ndjson")

    def _parse_date_hierarchy(self, date_str: str) -> tuple[str, str, str]:
        """Parses YYYY-MM-DD into (year, month, day) components."""
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            return dt.strftime("%Y"), dt.strftime("%m"), dt.strftime("%d")
        except Exception:
            parts = str(date_str).split("-")
            year = parts[0] if len(parts) > 0 else datetime.now(timezone.utc).strftime("%Y")
            month = parts[1] if len(parts) > 1 else datetime.now(timezone.utc).strftime("%m")
            day = parts[2][:2] if len(parts) > 2 else datetime.now(timezone.utc).strftime("%d")
            return year, month, day

    def get_gcs_blob_path(self, log_date_str: str, filename: str) -> str:
        """
        Builds the date-partitioned blob path in GCS with multi-year hierarchy.
        Pattern: {prefix}/year={YYYY}/month={MM}/day={DD}/{filename}
        Example: events/year=2026/month=09/day=04/2026-09-04.ndjson
        """
        year, month, day = self._parse_date_hierarchy(log_date_str)
        partition = f"year={year}/month={month}/day={day}"
        if self.gcs_prefix:
            return f"{self.gcs_prefix}/{partition}/{filename}"
        return f"{partition}/{filename}"

    def _cleanup_old_logs(self):
        """Purge log files older than retention_days."""
        try:
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if self._last_cleanup_date == today_str:
                return
            self._last_cleanup_date = today_str

            cutoff_date = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
            for filename in os.listdir(self.log_dir):
                if not filename.endswith(".ndjson"):
                    continue
                file_date_str = filename[:-7]
                try:
                    file_date = datetime.strptime(file_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    if file_date < cutoff_date:
                        file_path = os.path.join(self.log_dir, filename)
                        os.remove(file_path)
                        logger.info("[EventLogger] Purged expired log file: %s", filename)
                except ValueError:
                    continue
        except Exception as exc:
            logger.warning("[EventLogger] Error during retention cleanup: %s", exc)

    def _write_batch_sync(self, lines: list[str]):
        """Synchronously append lines to today's log file."""
        if not lines:
            return
        log_path = self._get_current_log_path()
        with open(log_path, "a", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")
        self._cleanup_old_logs()

    async def _worker_loop(self):
        """Background worker loop draining event queue in non-blocking batches."""
        logger.info("[EventLogger] Worker started. Writing events to %s", self.log_dir)
        batch = []
        while self.is_running or not self.queue.empty():
            try:
                # Wait for at least one item
                try:
                    event = await asyncio.wait_for(self.queue.get(), timeout=1.0)
                    batch.append(event)
                    self.queue.task_done()
                except asyncio.TimeoutError:
                    pass

                # Drain any remaining buffered items up to batch size 50
                while len(batch) < 50 and not self.queue.empty():
                    try:
                        e = self.queue.get_nowait()
                        batch.append(e)
                        self.queue.task_done()
                    except asyncio.QueueEmpty:
                        break

                if batch:
                    lines = [json.dumps(item, default=str, ensure_ascii=False) for item in batch]
                    await asyncio.to_thread(self._write_batch_sync, lines)
                    batch.clear()

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("[EventLogger] Worker error: %s", exc, exc_info=True)
                await asyncio.sleep(1.0)

        # Flush any remaining items on shutdown
        if batch:
            try:
                lines = [json.dumps(item, default=str, ensure_ascii=False) for item in batch]
                await asyncio.to_thread(self._write_batch_sync, lines)
            except Exception:
                pass
        logger.info("[EventLogger] Worker stopped.")

    async def _gcs_periodic_flush_loop(self):
        """Background worker loop periodically flushing daily NDJSON logs to GCS."""
        logger.info(
            "[EventLogger] GCS auto-flush loop active (interval: %ds, bucket: %s, partition: %s/dt=YYYY-MM-DD/)",
            self.flush_interval,
            self.bucket_name,
            self.gcs_prefix,
        )
        while self.is_running:
            try:
                await asyncio.sleep(self.flush_interval)
                if self.is_running and self.bucket_name:
                    logger.info("[EventLogger] Triggering scheduled GCS log flush...")
                    await self.flush_to_gcs()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("[EventLogger] Error in scheduled GCS flush loop: %s", exc)

    def start(self):
        """Start the background consumer task and periodic GCS flush task."""
        if self.is_running:
            return
        self.is_running = True
        self.worker_task = asyncio.create_task(self._worker_loop())
        if self.bucket_name and self.flush_interval > 0:
            self.gcs_task = asyncio.create_task(self._gcs_periodic_flush_loop())

    async def stop(self):
        """Gracefully stop and flush the event queue, then trigger final GCS flush."""
        if not self.is_running:
            return
        self.is_running = False

        if self.gcs_task:
            self.gcs_task.cancel()
            try:
                await self.gcs_task
            except asyncio.CancelledError:
                pass
            self.gcs_task = None

        if self.worker_task:
            self.worker_task.cancel()
            try:
                await self.worker_task
            except asyncio.CancelledError:
                pass
            self.worker_task = None

        # Final flush on graceful shutdown
        if self.bucket_name:
            try:
                await self.flush_to_gcs()
            except Exception as exc:
                logger.warning("[EventLogger] Final GCS flush on shutdown failed: %s", exc)

    async def emit(self, event_type: str, **kwargs):
        """
        Record a structured event into the queue. Non-blocking.
        """
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            **kwargs,
        }
        try:
            self.queue.put_nowait(payload)
        except asyncio.QueueFull:
            logger.warning("[EventLogger] Queue full. Dropping event: %s", event_type)

    def emit_sync(self, event_type: str, **kwargs):
        """Thread-safe synchronous emitter helper."""
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                loop.create_task(self.emit(event_type, **kwargs))
                return
        except RuntimeError:
            pass

        # Fallback if no running event loop
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            **kwargs,
        }
        try:
            line = json.dumps(payload, default=str, ensure_ascii=False)
            self._write_batch_sync([line])
        except Exception as exc:
            logger.warning("[EventLogger] Sync write fallback failed: %s", exc)

    def _get_gcs_storage_client(self):
        """Returns a GCS storage Client using service account credentials if available."""
        from google.cloud import storage
        cred_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if not cred_path or not os.path.exists(cred_path):
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            candidates = [
                "/app/secrets/gcs-credentials.json",
                os.path.join(base_dir, "secrets", "gcs-credentials.json"),
                "/opt/stl-voicebot/secrets/gcs-credentials.json"
            ]
            for c in candidates:
                if os.path.exists(c):
                    cred_path = c
                    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = c
                    break
        if cred_path and os.path.exists(cred_path):
            try:
                from google.auth import load_credentials_from_file
                credentials, _ = load_credentials_from_file(cred_path)
                return storage.Client(project=self.project_id, credentials=credentials)
            except Exception as e:
                logger.warning("[EventLogger] Failed to load credentials from %s: %s", cred_path, e)
        return storage.Client(project=self.project_id)

    async def flush_to_gcs(
        self, bucket_name: Optional[str] = None, target_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Flush daily log file to Google Cloud Storage under partitioned structure:
        gs://{bucket}/{prefix}/dt={YYYY-MM-DD}/{YYYY-MM-DD}.ndjson

        Returns dict describing operation status.
        """
        target_bucket = bucket_name or self.bucket_name
        if not target_bucket:
            logger.debug("[EventLogger] GCS flush skipped: GCS_LOG_BUCKET not configured.")
            return {"success": False, "reason": "GCS_LOG_BUCKET not configured"}

        date_str = target_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_file = f"{date_str}.ndjson"
        local_path = os.path.join(self.log_dir, log_file)

        if not os.path.exists(local_path):
            return {"success": False, "reason": f"Local log file {log_file} does not exist yet."}

        file_size = os.path.getsize(local_path)
        blob_path = self.get_gcs_blob_path(date_str, log_file)

        try:
            client = self._get_gcs_storage_client()
            bucket = client.bucket(target_bucket)
            blob = bucket.blob(blob_path)

            await asyncio.to_thread(blob.upload_from_filename, local_path, content_type="application/x-ndjson")

            now_iso = datetime.now(timezone.utc).isoformat()
            self.last_gcs_flush = now_iso
            self.last_gcs_status = "SUCCESS"
            self.last_gcs_error = None

            logger.info(
                "[EventLogger] Successfully uploaded %s (%d bytes) to gs://%s/%s",
                log_file,
                file_size,
                target_bucket,
                blob_path,
            )
            return {
                "success": True,
                "bucket": target_bucket,
                "blob": blob_path,
                "bytes": file_size,
                "timestamp": now_iso,
            }
        except ImportError:
            msg = "google-cloud-storage library is not installed in Python environment."
            logger.warning("[EventLogger] %s", msg)
            self.last_gcs_status = "FAILED"
            self.last_gcs_error = msg
            return {"success": False, "reason": msg}
        except Exception as exc:
            err_str = str(exc)
            if "403" in err_str or "scope" in err_str.lower():
                msg = (
                    f"GCS upload authorization error: 403 Forbidden. "
                    f"The VM instance access scope restricts Cloud Storage writes. "
                    f"Fix: Allow full API access on the VM or supply GOOGLE_APPLICATION_CREDENTIALS. Details: {err_str}"
                )
            else:
                msg = f"Failed to upload log to GCS: {err_str}"

            logger.error("[EventLogger] %s", msg)
            now_iso = datetime.now(timezone.utc).isoformat()
            self.last_gcs_flush = now_iso
            self.last_gcs_status = "FAILED"
            self.last_gcs_error = msg
            return {"success": False, "reason": msg}

    def get_status(self) -> Dict[str, Any]:
        """Returns diagnostic status of the EventLogger system."""
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        current_file = f"{today_str}.ndjson"
        current_path = os.path.join(self.log_dir, current_file)

        files = []
        if os.path.exists(self.log_dir):
            for f in sorted(os.listdir(self.log_dir)):
                if f.endswith(".ndjson"):
                    p = os.path.join(self.log_dir, f)
                    files.append({"file": f, "size_bytes": os.path.getsize(p)})

        return {
            "is_running": self.is_running,
            "log_dir": self.log_dir,
            "current_log_file": current_file,
            "current_file_exists": os.path.exists(current_path),
            "current_file_bytes": os.path.getsize(current_path) if os.path.exists(current_path) else 0,
            "queue_size": self.queue.qsize(),
            "retention_days": self.retention_days,
            "gcs_bucket": self.bucket_name,
            "gcs_prefix": self.gcs_prefix,
            "gcs_sample_partition_path": self.get_gcs_blob_path(today_str, current_file),
            "flush_interval_seconds": self.flush_interval,
            "last_gcs_flush": self.last_gcs_flush,
            "last_gcs_status": self.last_gcs_status,
            "last_gcs_error": self.last_gcs_error,
            "available_log_files": files,
        }

    def read_recent_events(self, limit: int = 100, target_date: Optional[str] = None) -> List[Dict[str, Any]]:
        """Reads recent JSON lines from a daily log file."""
        date_str = target_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        file_path = os.path.join(self.log_dir, f"{date_str}.ndjson")
        if not os.path.exists(file_path):
            return []

        lines = []
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            lines.append(json.loads(line))
                        except Exception:
                            continue
            return lines[-limit:]
        except Exception as exc:
            logger.warning("[EventLogger] Failed to read recent events: %s", exc)
            return []

    def clean_phone_number(self, phone: Optional[str]) -> str:
        """Sanitizes phone numbers for safe, readable filenames."""
        if not phone:
            return "unknown"
        # Preserve digits and plus, remove spaces and hyphens
        clean = "".join(c for c in str(phone).strip() if c.isdigit() or c == "+")
        return clean or "unknown"

    def get_telephony_provider(self, source: Optional[str], call_sid: Optional[str]) -> str:
        """Determines whether call originated via Tata Smartflo or Twilio."""
        s = (source or "").lower()
        sid = str(call_sid or "")
        if "smartflo" in s or "tata" in s or sid.startswith("CAGE3"):
            return "tata"
        if "twilio" in s or sid.startswith("CA"):
            return "twilio"
        return s or "telephony"

    def get_call_blob_path(self, date_str: str, call_id: Any, provider: str, phone: str) -> str:
        """Generates partitioned GCS blob path: calls/year=YYYY/month=MM/day=DD/call_{id}_{provider}_{phone}.json"""
        year, month, day = self._parse_date_hierarchy(date_str)
        clean_phone = self.clean_phone_number(phone)
        clean_provider = provider.lower().replace(" ", "_")
        filename = f"call_{call_id}_{clean_provider}_{clean_phone}.json"
        return f"calls/year={year}/month={month}/day={day}/{filename}"

    async def upload_call_json(self, call_data: Dict[str, Any], date_str: Optional[str] = None) -> Dict[str, Any]:
        """
        Saves a pretty-printed, indented JSON file for an individual call and uploads to GCS:
        gs://{bucket}/calls/year={YYYY}/month={MM}/day={DD}/call_{call_id}_{provider}_{phone}.json
        """
        target_bucket = self.bucket_name
        try:                                    # a tenant with its own bucket keeps its call logs there
            from .tenant_storage import bucket_for_tenant
            target_bucket = (await bucket_for_tenant(call_data.get("tenant_id"))) or target_bucket
        except Exception as exc:
            logger.debug("[EventLogger] tenant bucket lookup skipped: %s", exc)
        if not target_bucket:
            return {"success": False, "reason": "GCS_LOG_BUCKET not configured"}

        dt = date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        year, month, day = self._parse_date_hierarchy(dt)
        call_id = call_data.get("call_id") or "unknown"
        telephony = call_data.get("telephony", {})
        provider = telephony.get("provider") or self.get_telephony_provider(
            call_data.get("source"), telephony.get("call_sid") or call_data.get("call_sid")
        )
        phone = telephony.get("caller_phone") or call_data.get("caller_phone") or "unknown"

        blob_path = self.get_call_blob_path(dt, call_id, provider, phone)
        filename = os.path.basename(blob_path)

        # Save locally under logs/calls/year=YYYY/month=MM/day=DD/
        local_dir = os.path.join(os.path.dirname(self.log_dir), "calls", f"year={year}", f"month={month}", f"day={day}")
        os.makedirs(local_dir, exist_ok=True)
        local_file = os.path.join(local_dir, filename)

        pretty_json_str = json.dumps(call_data, indent=2, ensure_ascii=False)
        with open(local_file, "w", encoding="utf-8") as f:
            f.write(pretty_json_str)

        try:
            client = self._get_gcs_storage_client()
            bucket = client.bucket(target_bucket)
            blob = bucket.blob(blob_path)

            await asyncio.to_thread(
                blob.upload_from_string,
                pretty_json_str,
                content_type="application/json",
            )

            logger.info(
                "[EventLogger] Uploaded call log JSON to gs://%s/%s (%d bytes)",
                target_bucket,
                blob_path,
                len(pretty_json_str.encode("utf-8")),
            )
            return {
                "success": True,
                "bucket": target_bucket,
                "blob": blob_path,
                "filename": filename,
                "bytes": len(pretty_json_str.encode("utf-8")),
            }
        except Exception as exc:
            logger.error("[EventLogger] Failed to upload call JSON to GCS: %s", exc)
            return {"success": False, "reason": str(exc), "local_file": local_file}

    async def backfill_call_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Queries recent calls from the database and uploads their structured JSON files to GCS.
        """
        results = []
        try:
            from .database import async_session
            from .models import Call
            from sqlalchemy import select
            from sqlalchemy.orm import selectinload

            async with async_session() as session:
                stmt = (
                    select(Call)
                    .options(
                        selectinload(Call.transcript_messages),
                        selectinload(Call.tickets),
                    )
                    .order_by(Call.id.desc())
                    .limit(limit)
                )
                res = await session.execute(stmt)
                calls = res.scalars().all()

                for call in calls:
                    dt = (call.started_at or datetime.utcnow()).strftime("%Y-%m-%d")
                    provider = self.get_telephony_provider(call.source, call.call_sid)

                    transcripts = []
                    for t in (call.transcript_messages or []):
                        transcripts.append({
                            "sequence": t.sequence_number,
                            "speaker": f"Agent ({call.current_agent or 'Service Desk'})" if t.role == "agent" else f"Caller ({call.caller_number or 'User'})",
                            "role": t.role,
                            "text": t.message,
                        })

                    ticket_info = {"created": False, "ticket_id": None, "issue_summary": None, "priority": None}
                    if call.tickets:
                        latest = call.tickets[0]
                        ticket_info = {
                            "created": True,
                            "ticket_id": getattr(latest, "ticket_id", None) or getattr(latest, "id", None),
                            "issue_summary": getattr(latest, "issue", None) or getattr(latest, "issue_description", None),
                            "priority": getattr(latest, "priority", None),
                            "status": getattr(latest, "status", "created"),
                        }

                    call_dict = {
                        "call_id": call.id,
                        "tenant_id": getattr(call, "tenant_id", None),
                        "telephony": {
                            "provider": provider,
                            "channel": call.source,
                            "call_sid": call.call_sid,
                            "caller_phone": call.caller_number,
                            "called_phone": call.called_number,
                        },
                        "status": call.status,
                        "started_at": call.started_at.isoformat() if call.started_at else None,
                        "ended_at": call.ended_at.isoformat() if call.ended_at else None,
                        "duration_seconds": call.duration_seconds,
                        "summary": call.summary,
                        "routing": {
                            "final_agent": call.current_agent or "Incident Management Assistant",
                        },
                        "ticket": ticket_info,
                        "transcript": transcripts,
                        "telephony_stats": {
                            "is_escalated": call.is_escalated,
                            "transferred_to_human": call.transferred_to_human,
                            "sentiment": call.sentiment,
                            "intent": call.intent,
                        },
                        "call_kpis": self.estimate_call_kpis({
                            "transcript": transcripts,
                            "duration_seconds": call.duration_seconds,
                            "ticket": ticket_info,
                        }),
                    }

                    upload_res = await self.upload_call_json(call_dict, date_str=dt)
                    results.append(upload_res)

        except Exception as exc:
            logger.error("[EventLogger] Backfill error: %s", exc, exc_info=True)
            results.append({"success": False, "error": str(exc)})

        return results

    @staticmethod
    def estimate_call_kpis(call_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Compute estimated per-call KPIs from available fields when live metrics aren't available."""
        transcript = call_dict.get("transcript") or []
        turns = len([t for t in transcript if isinstance(t, dict) and t.get("role") == "user"])
        agent_turns = len([t for t in transcript if isinstance(t, dict) and t.get("role") in ("agent", "assistant")])
        total_chars = sum(len(t.get("text", "")) for t in transcript if isinstance(t, dict) and t.get("role") in ("agent", "assistant"))
        duration = call_dict.get("duration_seconds") or 60.0
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

        has_ticket = bool((call_dict.get("ticket") or {}).get("created"))
        avg_turn_latency = 1657.0 if not has_ticket else 2800.0

        return {
            "turn_count": turns,
            "interruption_count": 0,
            "stt_latency_ms": {"avg": 210.0, "median": 200.0},
            "llm_route_latency_ms": {"avg": avg_turn_latency, "median": avg_turn_latency * 0.85},
            "tts_ttfb_ms": {"avg": 180.0, "median": 160.0},
            "total_turn_latency_ms": {"avg": avg_turn_latency + 390.0, "median": (avg_turn_latency + 390.0) * 0.9},
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
            "source": "estimated",
        }

    async def upload_call_by_id(self, call_id: int) -> Dict[str, Any]:
        """
        Loads a single call with transcripts and tickets by its ID and uploads to GCS immediately.
        """
        try:
            from .database import async_session
            from .models import Call
            from sqlalchemy import select
            from sqlalchemy.orm import selectinload

            async with async_session() as session:
                stmt = (
                    select(Call)
                    .options(
                        selectinload(Call.transcript_messages),
                        selectinload(Call.tickets),
                    )
                    .where(Call.id == call_id)
                )
                res = await session.execute(stmt)
                call = res.scalars().first()
                if not call:
                    return {"success": False, "reason": f"Call {call_id} not found"}

                dt = (call.started_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
                provider = self.get_telephony_provider(call.source, call.call_sid)

                transcripts = []
                for t in (call.transcript_messages or []):
                    transcripts.append({
                        "sequence": t.sequence_number,
                        "speaker": f"Agent ({call.current_agent or 'Service Desk'})" if t.role == "agent" else f"Caller ({call.caller_number or 'User'})",
                        "role": t.role,
                        "text": t.message,
                    })

                ticket_info = {"created": False, "ticket_id": None, "issue_summary": None, "priority": None}
                if call.tickets:
                    latest = call.tickets[0]
                    ticket_info = {
                        "created": True,
                        "ticket_id": getattr(latest, "ticket_id", None) or getattr(latest, "id", None),
                        "issue_summary": getattr(latest, "issue", None) or getattr(latest, "issue_description", None),
                        "priority": getattr(latest, "priority", None),
                        "status": getattr(latest, "status", "created"),
                    }

                call_dict = {
                    "call_id": call.id,
                    "telephony": {
                        "provider": provider,
                        "channel": call.source,
                        "call_sid": call.call_sid,
                        "caller_phone": call.caller_number,
                        "called_phone": call.called_number,
                    },
                    "status": call.status,
                    "started_at": call.started_at.isoformat() if call.started_at else None,
                    "ended_at": call.ended_at.isoformat() if call.ended_at else None,
                    "duration_seconds": call.duration_seconds,
                    "summary": call.summary,
                    "routing": {
                        "final_agent": call.current_agent or "Incident Management Assistant",
                    },
                    "ticket": ticket_info,
                    "transcript": transcripts,
                    "telephony_stats": {
                        "is_escalated": call.is_escalated,
                        "transferred_to_human": call.transferred_to_human,
                        "sentiment": call.sentiment,
                        "intent": call.intent,
                    },
                    "call_kpis": self.estimate_call_kpis({
                        "transcript": transcripts,
                        "duration_seconds": call.duration_seconds,
                        "ticket": ticket_info,
                    }),
                }

                return await self.upload_call_json(call_dict, date_str=dt)
        except Exception as exc:
            logger.error("[EventLogger] Error uploading call %s: %s", call_id, exc, exc_info=True)
            return {"success": False, "error": str(exc)}


# Global singleton instance
event_logger = EventLogger()
