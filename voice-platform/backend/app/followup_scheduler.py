"""Sanjeevani patient-medication outbound follow-up scheduler.

Mirrors ``call_reaper.py``: a single ``while True`` background worker, started in ``main.py``'s
lifespan and guarded by ``SANJEEVANI_SCHEDULER_ENABLED`` (default off, so it is inert until a tenant
opts in). Each tick it picks the follow-up tasks that are due *inside the tenant's calling window
(in the tenant's own time zone)*, locks each row so a restart can never double-call, and places the
call on the existing outbound telephony path.

Everything here is written to be unit-testable with a fake clock: the pure helpers
(:func:`clamp_to_calling_hours`, :func:`within_window`, :func:`next_attempt_after`) take explicit
arguments, and :func:`run_due_tasks` / :func:`update_task_from_call` take an injectable ``now`` and
an injectable call ``placer`` so no test ever touches the network.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, time, timedelta, timezone
from typing import Awaitable, Callable, List, Optional

from sqlalchemy import func, or_, select

from .database import async_session
from .models import (
    Call,
    CallStatus,
    FollowUpTask,
    Patient,
    Prescription,
    PrescriptionItem,
    Tenant,
)

logger = logging.getLogger(__name__)

# ── Tunables (read on demand so a test / deploy can change them without re-import) ──────────
SWEEP_EVERY_SECS = int(os.getenv("SANJEEVANI_SCHEDULER_SWEEP_SECS", "10"))
LOCK_RECLAIM_MIN = float(os.getenv("SANJEEVANI_LOCK_RECLAIM_MIN", "10"))

# Retry backoff after a no-answer: +2h, next day, +2 days; after max_attempts -> needs_human.
RETRY_BACKOFF = (timedelta(hours=2), timedelta(days=1), timedelta(days=2))

_OFFSET_MAP = {  # last-resort fixed offsets if the IANA tz database isn't installed (e.g. bare Windows)
    "Asia/Kolkata": timedelta(hours=5, minutes=30),
    "Asia/Dubai": timedelta(hours=4),
    "Asia/Muscat": timedelta(hours=4),
    "Asia/Riyadh": timedelta(hours=3),
    "Asia/Qatar": timedelta(hours=3),
    "Asia/Kuwait": timedelta(hours=3),
    "Asia/Bahrain": timedelta(hours=3),
    "UTC": timedelta(0),
}


def scheduler_enabled() -> bool:
    return os.getenv("SANJEEVANI_SCHEDULER_ENABLED", "false").strip().lower() == "true"


def sanjeevani_workflow_id() -> Optional[int]:
    raw = (os.getenv("SANJEEVANI_WORKFLOW_ID") or "").strip()
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


async def workflow_for_tenant(db, tenant_id: Optional[int]) -> Optional[int]:
    """The agent that makes this company's follow-up calls: SANJEEVANI_WORKFLOW_ID when set (one shared agent),
    otherwise the company's own live agent."""
    forced = sanjeevani_workflow_id()
    if forced is not None:
        return forced
    from .models import Workflow
    q = select(Workflow.id).where(Workflow.is_active == True)  # noqa: E712
    if tenant_id is not None:
        q = q.where(Workflow.tenant_id == tenant_id)
    return (await db.execute(q.order_by(Workflow.id))).scalars().first()


def explain_delay_seconds() -> int:
    """How long after a prescription has been read and saved the first call goes out (default 180 s)."""
    try:
        return max(0, int(os.getenv("SANJEEVANI_EXPLAIN_DELAY_SECONDS", "180")))
    except ValueError:
        return 180


async def tenant_window(db, tenant_id: Optional[int]):
    """(start, end) of the company's calling hours as "HH:MM" strings; 09:00 to 20:00 unless the company set its own."""
    if tenant_id is not None:
        t = await db.get(Tenant, tenant_id)
        w = ((t.settings_json or {}).get("calling_window") if t else None) or {}
        if isinstance(w, dict) and w.get("start") and w.get("end"):
            return str(w["start"]), str(w["end"])
    return "09:00", "20:00"


def feedback_offset_days() -> int:
    try:
        return int(os.getenv("SANJEEVANI_FEEDBACK_OFFSET_DAYS", "1"))
    except ValueError:
        return 1


def visit_feedback_delay_days() -> int:
    """How many days after the clinic marks a visit done the feedback call goes out (default 2)."""
    try:
        return max(0, int(os.getenv("SANJEEVANI_VISIT_FEEDBACK_DAYS", "2")))
    except ValueError:
        return 2


def daily_call_cap() -> int:
    try:
        return int(os.getenv("SANJEEVANI_DAILY_CALL_CAP", "200"))
    except ValueError:
        return 200


def max_concurrent_calls() -> int:
    try:
        return int(os.getenv("SANJEEVANI_MAX_CONCURRENT_CALLS", "5"))
    except ValueError:
        return 5


# ── Time-zone helpers ──────────────────────────────────────────────────────────────────────
def resolve_tz(name: Optional[str]):
    """A tzinfo for an IANA name, degrading gracefully when the tz database is missing."""
    tzname = (name or "Asia/Kolkata").strip() or "Asia/Kolkata"
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(tzname)
    except Exception:
        return timezone(_OFFSET_MAP.get(tzname, timedelta(0)))


async def tenant_timezone(db, tenant_id: Optional[int]):
    tzname = None
    if tenant_id is not None:
        t = await db.get(Tenant, tenant_id)
        tzname = getattr(t, "timezone", None) if t else None
    return resolve_tz(tzname)


def _parse_hhmm(hhmm: str, default=(9, 0)):
    try:
        h, m = str(hhmm).split(":")
        return int(h), int(m)
    except Exception:
        return default


def _as_utc_aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def clamp_to_calling_hours(dt_utc: datetime, tz, window_start: str = "09:00",
                           window_end: str = "20:00") -> datetime:
    """Move ``dt_utc`` forward (if needed) to the next moment inside the local calling window.

    Returns a naive-UTC datetime (the convention the models store)."""
    local = _as_utc_aware(dt_utc).astimezone(tz)
    sh, sm = _parse_hhmm(window_start, (9, 0))
    eh, em = _parse_hhmm(window_end, (20, 0))
    start = local.replace(hour=sh, minute=sm, second=0, microsecond=0)
    end = local.replace(hour=eh, minute=em, second=0, microsecond=0)
    if local < start:
        target = start
    elif local >= end:
        target = start + timedelta(days=1)
    else:
        target = local
    return target.astimezone(timezone.utc).replace(tzinfo=None)


def within_window(dt_utc: datetime, tz, window_start: str = "09:00",
                  window_end: str = "20:00") -> bool:
    local_t = _as_utc_aware(dt_utc).astimezone(tz).time()
    sh, sm = _parse_hhmm(window_start, (9, 0))
    eh, em = _parse_hhmm(window_end, (20, 0))
    start, end = time(sh, sm), time(eh, em)
    if start <= end:
        return start <= local_t < end
    return local_t >= start or local_t < end          # window that wraps past midnight


def next_attempt_after(attempts: int, max_attempts: int, now_utc: datetime):
    """(status, next_attempt_at) after a no-answer. ``attempts`` is the count already made."""
    if attempts >= max_attempts or attempts > len(RETRY_BACKOFF):
        return "needs_human", None
    return "no_answer", now_utc + RETRY_BACKOFF[attempts - 1]


# ── Spoken context block (brief §10): what the agent is given about the prescription ─────────
def build_message_context(patient: Patient, prescription: Prescription,
                          items: List[PrescriptionItem], kind: str) -> str:
    """Structured block passed as the call's ``message_context``.

    Doses are read **verbatim** from the record. Items flagged ``needs_clarification`` are NOT
    read out — the agent is told the pharmacist will confirm them, and no dose is included."""
    first = (patient.first_name or "there").strip()
    lines = [
        f"TASK: {kind}",
        f"PATIENT_FIRST_NAME: {first}",
        f"LANGUAGE: {patient.preferred_language or 'en'}",
    ]
    if patient.guardian_name:
        lines.append(f"GUARDIAN: {patient.guardian_name}")
    if prescription.doctor_name:
        lines.append(f"PRESCRIBING_DOCTOR: {prescription.doctor_name}")
    if prescription.facility:
        lines.append(f"FACILITY: {prescription.facility}")
    lines.append("MEDICINES:")
    for it in items:
        name = " ".join(x for x in (it.drug_name, it.strength, it.form) if x) or "medicine"
        if (it.status or "").lower() == "needs_clarification":
            lines.append(f"- {name}: the pharmacist will confirm this one — do NOT state a dose."
                         + (f" (open question: {it.open_question})" if it.open_question else ""))
            continue
        bits = []
        if it.dose_text:
            bits.append(it.dose_text)
        if it.timing:
            bits.append(it.timing)
        if it.duration_days:
            bits.append(f"for {it.duration_days} days")
        detail = ", ".join(bits) if bits else "as directed"
        plain = (it.plain_summary_ar if (patient.preferred_language or "en").startswith("ar")
                 else it.plain_summary_en)
        line = f"- {name}: {detail}."
        if plain:
            line += f" {plain.strip()}"
        if it.warnings:
            line += f" (note: {it.warnings.strip()})"
        lines.append(line)
    if prescription.extra_instruction:
        lines.append(f"EXTRA: {prescription.extra_instruction.strip()}")
    return "\n".join(lines)


def build_call_brief(patient: Patient, prescription: Optional[Prescription], kind: str, task_id: int, meeting=None) -> str:
    """What the agent is told when the call connects. It holds NO medicine, dose or condition: those come from
    the get_prescription_context tool, which refuses until the person on the line has confirmed the date of birth.
    (Anything put in here would reach the model before the identity check, and could be said to the wrong person.)"""
    contact = (patient.guardian_name or patient.first_name or "the patient").strip()
    lines = [
        f"CALL TYPE: {kind}",
        f"TASK ID: {task_id}",
        f"SPEAK TO: {contact}" + (f" (about {patient.first_name})" if patient.guardian_name and patient.first_name else ""),
        f"LANGUAGE: {patient.preferred_language or 'en'} (ask which language they prefer, and switch if they do)",
    ]
    if prescription is not None and prescription.doctor_name:
        lines.append(f"PRESCRIBING DOCTOR: {prescription.doctor_name}")
    if meeting is not None:
        lines.append(f"VISIT: the patient saw {meeting.doctor_name or 'the doctor'}" + (f" ({meeting.booked_slot})" if meeting.booked_slot else ""))
        lines.append(f"MEETING ID: {meeting.id}")
        lines += [
            "",
            "ORDER OF THE CALL: 1) say you are an automated assistant and the call may be recorded; 2) ask for the date of birth "
            "and call confirm_identity (two tries at most; if it fails, say you will try another time, share nothing and end the call); "
            "3) only then ask how the visit went, whether the medicine the doctor gave them worked well, and how their overall experience "
            "was from 1 to 5; 4) call record_visit_feedback with the MEETING ID above and what they said. If they are unhappy, say you are "
            "sorry and that the care team will arrange a follow-up with the doctor; give no medical advice.",
        ]
        return "\n".join(lines)
    lines += [
        "",
        "ORDER OF THE CALL: 1) say you are an automated assistant and the call may be recorded; 2) ask for the date of birth "
        "and call confirm_identity (two tries at most; if it fails, say you will try another time, share nothing and end the call); "
        "3) only then call get_prescription_context to get the medicines and follow the steps for the call type; "
        "4) record what happens with the tools. Never say a medicine, dose or condition before identity is confirmed.",
    ]
    return "\n".join(lines)


# ── Task creation (brief §5 / plan Phase 2.2); shared with prescriptions_api ────────────────
async def create_followup_tasks(db, patient: Patient, prescription: Prescription,
                                items: List[PrescriptionItem], now: Optional[datetime] = None
                                ) -> List[FollowUpTask]:
    """One ``explain`` task due now (clamped into calling hours); one ``feedback`` task per DISTINCT
    item end_date (items sharing an end date are grouped). Items with no end_date get no feedback
    call until the duration is known (brief KB-07)."""
    now = now or datetime.utcnow()
    tz = await tenant_timezone(db, prescription.tenant_id)
    w_start, w_end = await tenant_window(db, prescription.tenant_id)
    created: List[FollowUpTask] = []

    # The first call goes out a fixed time after the prescription was read and saved (180 s by default), inside calling hours.
    explain = FollowUpTask(
        tenant_id=prescription.tenant_id, patient_id=patient.id, prescription_id=prescription.id,
        kind="explain", due_at=clamp_to_calling_hours(now + timedelta(seconds=explain_delay_seconds()), tz, w_start, w_end),
        status="pending", window_start=w_start, window_end=w_end,
    )
    db.add(explain)
    created.append(explain)

    offset = feedback_offset_days()
    end_dates = sorted({it.end_date for it in items if it.end_date is not None})
    for ed in end_dates:
        due = clamp_to_calling_hours(ed + timedelta(days=offset), tz, w_start, w_end)
        fb = FollowUpTask(
            tenant_id=prescription.tenant_id, patient_id=patient.id, prescription_id=prescription.id,
            kind="feedback", due_at=due, status="pending", window_start=w_start, window_end=w_end,
        )
        db.add(fb)
        created.append(fb)

    await db.flush()
    return created


async def schedule_visit_feedback(db, meeting, now: Optional[datetime] = None) -> Optional[FollowUpTask]:
    """The clinic has marked a visit done: one feedback call, a few days later (2 by default), inside calling hours.
    Returns the task, or None when there is nobody to call or the visit already has one."""
    from .models import DoctorMeetingRequest
    if meeting is None or meeting.patient_id is None:
        return None
    existing = (await db.execute(select(FollowUpTask).where(
        FollowUpTask.meeting_id == meeting.id, FollowUpTask.kind == "feedback",
        FollowUpTask.status != "cancelled"))).scalars().first()
    if existing is not None:
        return None
    now = now or datetime.utcnow()
    tz = await tenant_timezone(db, meeting.tenant_id)
    w_start, w_end = await tenant_window(db, meeting.tenant_id)
    task = FollowUpTask(
        tenant_id=meeting.tenant_id, patient_id=meeting.patient_id, meeting_id=meeting.id, kind="feedback",
        due_at=clamp_to_calling_hours(now + timedelta(days=visit_feedback_delay_days()), tz, w_start, w_end),
        status="pending", window_start=w_start, window_end=w_end)
    db.add(task)
    await db.flush()
    return task


async def cancel_visit_feedback(db, meeting) -> int:
    """A visit that is no longer done (re-opened or cancelled) should not be followed by a call. Returns how many were cancelled."""
    rows = (await db.execute(select(FollowUpTask).where(
        FollowUpTask.meeting_id == meeting.id, FollowUpTask.status.in_(("pending", "no_answer"))))).scalars().all()
    for t in rows:
        t.status = "cancelled"
        t.outcome_json = {**(t.outcome_json or {}), "cancelled_reason": "visit_reopened"}
    return len(rows)


# ── Placing the call ────────────────────────────────────────────────────────────────────────
Placer = Callable[[object, FollowUpTask], Awaitable[Optional[int]]]


async def place_followup_call(db, task: FollowUpTask) -> Optional[int]:
    """Default placer: build the spoken context, place the call on the shared outbound path, store the
    context for ``media_bridge`` to pick up, and record a Call row. Returns the Call id."""
    from .api_routes import _place_outbound_call
    from .outbound_api import store_outbound_context

    patient = await db.get(Patient, task.patient_id)
    prescription = await db.get(Prescription, task.prescription_id) if task.prescription_id else None
    items: List[PrescriptionItem] = []
    if prescription is not None:
        items = (await db.execute(
            select(PrescriptionItem).where(PrescriptionItem.prescription_id == prescription.id)
        )).scalars().all()

    if not patient or not patient.phone_e164:
        logger.warning("[Sanjeevani] task %s has no patient phone; marking needs_human", task.id)
        task.status = "needs_human"
        await db.commit()
        return None

    meeting = None
    if getattr(task, "meeting_id", None):
        from .models import DoctorMeetingRequest
        meeting = await db.get(DoctorMeetingRequest, task.meeting_id)
    message_context = build_call_brief(patient, prescription, task.kind, task.id, meeting)
    metadata = {"task_id": task.id, "prescription_id": task.prescription_id, "meeting_id": task.meeting_id,
                "kind": task.kind, "patient_id": task.patient_id, "tenant_id": task.tenant_id}
    contact = (patient.guardian_name or patient.first_name or "").strip() or None
    greeting = ("Hello, this is an automated assistant calling from your doctor's clinic. This call may be recorded. "
                + (f"May I speak with {contact}?" if contact else "Am I speaking with the right person?"))
    workflow_id = await workflow_for_tenant(db, task.tenant_id)
    if workflow_id is None:
        # Without this the call would be answered by the platform's own live agent: another company's script on a patient.
        logger.warning("[Sanjeevani] task %s: company %s has no live agent; not calling", task.id, task.tenant_id)
        task.status = "needs_human"
        task.outcome_json = {**(task.outcome_json or {}), "cancelled_reason": "no_live_agent"}
        task.locked_at = None
        await db.commit()
        return None
    outbound_ctx = {
        "recipient_name": contact,
        "message_context": message_context,
        "greeting": greeting,
        "system_prompt": None,
        "voice": os.getenv("SANJEEVANI_VOICE", "Aarohi"),
        "callback_url": os.getenv("SANJEEVANI_CALLBACK_URL") or None,
        "metadata": metadata,
        "workflow_id": workflow_id,
        "tenant_id": task.tenant_id,
        "created_at": datetime.utcnow().isoformat(),
    }

    caller_id = None
    if task.tenant_id is not None:
        try:
            from .numbers import pick_caller_id

            caller_id = await pick_caller_id(db, task.tenant_id)
        except Exception as exc:
            logger.debug("[Sanjeevani] pick_caller_id skipped: %s", exc)

    call_result = await _place_outbound_call(patient.phone_e164, caller_id=caller_id)
    clean_e164 = call_result.get("clean_e164", patient.phone_e164)
    call_sid = (call_result.get("call_sid") or call_result.get("ref_id")
                or f"sanjeevani-{int(datetime.utcnow().timestamp())}")

    db_call = Call(
        call_sid=call_sid, caller_number=caller_id, called_number=clean_e164,
        tenant_id=task.tenant_id, status=CallStatus.ACTIVE, live_status="queued",
        source="sanjeevani-followup", current_agent="Patient care assistant",
        metadata_json=metadata, started_at=datetime.utcnow(),
    )
    db.add(db_call)
    await db.commit()

    # The carrier's media stream announces itself with its own call id, not the one the dial request returned, so the
    # stream finds this call by the context stored under the phone number, and takes this row (call_row_id) over.
    outbound_ctx["call_row_id"] = db_call.id
    await store_outbound_context(clean_e164, outbound_ctx)
    if call_sid:
        await store_outbound_context(call_sid, outbound_ctx)
    return db_call.id


# ── Cancel rules ─────────────────────────────────────────────────────────────────────────────
async def _cancel_reason(db, task: FollowUpTask) -> Optional[str]:
    patient = await db.get(Patient, task.patient_id)
    if patient and patient.do_not_call:
        return "patient_do_not_call"
    if task.prescription_id:
        presc = await db.get(Prescription, task.prescription_id)
        if presc and (presc.status or "").lower() == "cancelled":
            return "prescription_cancelled"
    return None


# ── One scheduler tick ───────────────────────────────────────────────────────────────────────
async def select_due_tasks(db, now: datetime) -> List[FollowUpTask]:
    stmt = select(FollowUpTask).where(
        or_(
            (FollowUpTask.status == "pending") & (FollowUpTask.due_at <= now),
            (FollowUpTask.status == "no_answer") & (FollowUpTask.next_attempt_at.isnot(None))
            & (FollowUpTask.next_attempt_at <= now),
        )
    ).order_by(FollowUpTask.due_at)
    return (await db.execute(stmt)).scalars().all()


async def reclaim_stale_locks(db, now: datetime) -> int:
    """A worker that crashed mid-call leaves a row 'calling'. Reclaim rows locked long ago."""
    cutoff = now - timedelta(minutes=LOCK_RECLAIM_MIN)
    rows = (await db.execute(select(FollowUpTask).where(
        FollowUpTask.status == "calling", FollowUpTask.locked_at.isnot(None),
        FollowUpTask.locked_at <= cutoff))).scalars().all()
    for r in rows:
        r.status = "no_answer" if r.attempts else "pending"
        r.locked_at = None
    if rows:
        await db.commit()
    return len(rows)


async def run_due_tasks(db, now: Optional[datetime] = None, placer: Optional[Placer] = None) -> int:
    """Place calls for all due, in-window tasks, honouring concurrency + daily cap. Returns count placed."""
    now = now or datetime.utcnow()
    placer = placer or place_followup_call

    await reclaim_stale_locks(db, now)

    in_flight = (await db.execute(
        select(func.count(FollowUpTask.id)).where(FollowUpTask.status == "calling"))).scalar() or 0
    concurrency_slots = max(0, max_concurrent_calls() - in_flight)
    if concurrency_slots <= 0:
        return 0

    midnight = datetime(now.year, now.month, now.day)
    placed_today = (await db.execute(select(func.count(FollowUpTask.id)).where(
        FollowUpTask.locked_at.isnot(None), FollowUpTask.locked_at >= midnight))).scalar() or 0
    day_budget = max(0, daily_call_cap() - placed_today)
    if day_budget <= 0:
        return 0

    placed = 0
    for task in await select_due_tasks(db, now):
        if placed >= concurrency_slots or placed >= day_budget:
            break

        reason = await _cancel_reason(db, task)
        if reason:
            task.status = "cancelled"
            task.outcome_json = {**(task.outcome_json or {}), "cancelled_reason": reason}
            await db.commit()
            continue

        tz = await tenant_timezone(db, task.tenant_id)
        if not within_window(now, tz, task.window_start, task.window_end):
            continue

        # Lock the row before calling (restart-safe): commit the claim first.
        task.status = "calling"
        task.locked_at = now
        task.attempts = (task.attempts or 0) + 1
        await db.commit()

        try:
            call_id = await placer(db, task)
        except Exception as exc:
            logger.warning("[Sanjeevani] placing task %s failed: %s", task.id, exc)
            task.status = "no_answer"
            task.next_attempt_at = now + RETRY_BACKOFF[0]
            await db.commit()
            continue

        if call_id is not None:
            task.last_call_id = call_id
            await db.commit()
        placed += 1

    return placed


async def update_task_from_call(task_id: int, outcome, now: Optional[datetime] = None) -> Optional[str]:
    """Settle a task once its call ends. ``outcome`` is 'done'/'answered'/'completed' (success) or
    anything else (no answer / failed). Returns the new status, or None if the task is gone.

    Wired through :func:`settle_followup_call`, which ``media_bridge`` calls when the call ends."""
    now = now or datetime.utcnow()
    async with async_session() as db:
        task = await db.get(FollowUpTask, task_id)
        if task is None:
            return None
        if (task.status or "") == "cancelled" or (
                (task.status or "") == "pending" and (task.outcome_json or {}).get("callback_requested")):
            return task.status             # cancelled (do not call), or moved to a later time by the patient during the call
        label = outcome if isinstance(outcome, str) else (outcome or {}).get("status", "")
        success = str(label).strip().lower() in ("done", "answered", "completed", "success")
        task.locked_at = None
        if success:
            task.status = "done"
            task.next_attempt_at = None
        else:
            task.status, task.next_attempt_at = next_attempt_after(
                task.attempts or 0, task.max_attempts or 3, now)
        task.outcome_json = {**(task.outcome_json or {}),
                             "last_outcome": label, "settled_at": now.isoformat()}
        await db.commit()
        return task.status


async def settle_followup_call(call_id: int, now: Optional[datetime] = None) -> Optional[str]:
    """Called when a call ends: if it was a follow-up call, settle its task from what actually happened.

    identity confirmed               -> done
    identity failed twice            -> try again later (counts as an attempt)
    patient spoke, checks unfinished -> try again later
    nobody spoke (no answer, voicemail) -> no answer, try again later
    """
    from .models import TranscriptMessage
    async with async_session() as db:
        call = await db.get(Call, call_id)
        meta = (call.metadata_json or {}) if call else {}
        task_id = meta.get("task_id")
        if not task_id:
            return None
        spoke = (await db.execute(select(func.count(TranscriptMessage.id)).where(
            TranscriptMessage.call_id == call_id, TranscriptMessage.role == "user"))).scalar() or 0
        if meta.get("identity_verified"):
            outcome = "done"
        elif meta.get("identity_failed"):
            outcome = "identity_failed"
        elif spoke:
            outcome = "incomplete"
        else:
            outcome = "no_answer"
        task = await db.get(FollowUpTask, int(task_id))
        if task is not None:
            task.last_call_id = call_id
            await db.commit()
    status = await update_task_from_call(int(task_id), outcome, now=now)
    if status == "done":
        await _mark_prescription_progress(int(task_id))
    return status


async def _mark_prescription_progress(task_id: int) -> None:
    async with async_session() as db:
        task = await db.get(FollowUpTask, task_id)
        presc = await db.get(Prescription, task.prescription_id) if task and task.prescription_id else None
        if presc is None:
            return
        if task.kind == "explain" and (presc.status or "issued") == "issued":
            presc.status = "explained"
        elif task.kind == "feedback":
            presc.status = "completed"
        await db.commit()


# ── Background worker (mirrors call_reaper.reaper_loop) ──────────────────────────────────────
async def followup_worker_loop() -> None:
    """Started in main.py's lifespan, guarded by SANJEEVANI_SCHEDULER_ENABLED (inert when off)."""
    if not scheduler_enabled():
        logger.info("[Sanjeevani] follow-up scheduler disabled (SANJEEVANI_SCHEDULER_ENABLED != true)")
        return
    logger.info("[Sanjeevani] follow-up scheduler started")
    while True:
        try:
            await asyncio.sleep(SWEEP_EVERY_SECS)
            async with async_session() as db:
                await run_due_tasks(db)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[Sanjeevani] scheduler tick failed: %s", exc)
