"""Sanjeevani management reporting (brief §8).

What the clinic's managers look at: how the follow-up programme is doing. All figures are read from
``medicine_feedback`` + ``follow_up_tasks`` and are tenant-scoped — the router is mounted behind
``_data_dep`` in ``main.py``, so the ORM read filter already limits every query to the caller's
tenant; we also apply the request scope explicitly for defence in depth.
"""
from __future__ import annotations

import logging

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db
from datetime import datetime

from .models import (
    DoctorMeetingRequest,
    FollowUpTask,
    MedicineFeedback,
    MedicineOrder,
    Patient,
    Prescription,
    PrescriptionItem,
    Tenant,
)
from .tenancy import request_scope

logger = logging.getLogger(__name__)

reports_router = APIRouter(prefix="/api/sanjeevani", tags=["sanjeevani-reports"])

_ATTEMPTED = ("calling", "done", "no_answer", "needs_human")


def _scoped(query, model):
    return request_scope().apply(query, model)


@reports_router.get("/reports/summary")
async def reports_summary(db: AsyncSession = Depends(get_db)):
    # Feedback calls: made (attempted at least once) vs answered (completed).
    made = (await db.execute(_scoped(
        select(func.count(FollowUpTask.id)).where(
            FollowUpTask.kind == "feedback", FollowUpTask.status.in_(_ATTEMPTED)),
        FollowUpTask))).scalar() or 0
    answered = (await db.execute(_scoped(
        select(func.count(FollowUpTask.id)).where(
            FollowUpTask.kind == "feedback", FollowUpTask.status == "done"),
        FollowUpTask))).scalar() or 0

    feedbacks = (await db.execute(_scoped(select(MedicineFeedback), MedicineFeedback))).scalars().all()
    helped_known = [f for f in feedbacks if f.helped]
    recovered = sum(1 for f in helped_known if f.helped == "yes")
    pct_recovered = round(100.0 * recovered / len(helped_known), 1) if helped_known else None

    sats = [f.satisfaction for f in feedbacks if f.satisfaction is not None]
    avg_satisfaction = round(sum(sats) / len(sats), 2) if sats else None

    # Side-effect mentions grouped by medicine (join feedback -> item).
    side_rows = [f for f in feedbacks if (f.side_effects_text or "").strip()]
    item_ids = {f.prescription_item_id for f in side_rows if f.prescription_item_id}
    item_names = {}
    if item_ids:
        items = (await db.execute(_scoped(
            select(PrescriptionItem).where(PrescriptionItem.id.in_(item_ids)),
            PrescriptionItem))).scalars().all()
        item_names = {it.id: it.drug_name or "unknown" for it in items}
    side_by_med: dict = {}
    for f in side_rows:
        med = item_names.get(f.prescription_item_id, "unspecified")
        side_by_med[med] = side_by_med.get(med, 0) + 1

    meetings_requested = (await db.execute(_scoped(
        select(func.count(DoctorMeetingRequest.id)), DoctorMeetingRequest))).scalar() or 0
    meetings_booked = (await db.execute(_scoped(
        select(func.count(DoctorMeetingRequest.id)).where(
            DoctorMeetingRequest.status.in_(("booked", "done"))), DoctorMeetingRequest))).scalar() or 0

    unreachable = (await db.execute(_scoped(
        select(func.count(func.distinct(FollowUpTask.patient_id))).where(
            FollowUpTask.status.in_(("no_answer", "needs_human"))), FollowUpTask))).scalar() or 0

    # Where every follow-up stands, and how many explain calls got through.
    by_status = {}
    for status, n in (await db.execute(_scoped(
            select(FollowUpTask.status, func.count(FollowUpTask.id)).group_by(FollowUpTask.status),
            FollowUpTask))).all():
        by_status[status or "unknown"] = n
    explained = (await db.execute(_scoped(
        select(func.count(FollowUpTask.id)).where(
            FollowUpTask.kind == "explain", FollowUpTask.status == "done"), FollowUpTask))).scalar() or 0
    prescriptions = (await db.execute(_scoped(select(func.count(Prescription.id)), Prescription))).scalar() or 0

    return {
        "tasks_by_status": by_status,
        "explain_calls_done": explained,
        "prescriptions": prescriptions,
        "feedback_calls_made": made,
        "feedback_calls_answered": answered,
        "percent_recovered": pct_recovered,
        "average_satisfaction": avg_satisfaction,
        "side_effects_by_medicine": [{"medicine": k, "mentions": v}
                                     for k, v in sorted(side_by_med.items(), key=lambda kv: -kv[1])],
        "meetings_requested": meetings_requested,
        "meetings_booked": meetings_booked,
        "unreachable_patients": unreachable,
        "total_feedback_records": len(feedbacks),
    }


# ── What patients said, and the doctor meetings it led to ────────────────────────────────────────────
def _names(rows, model):
    return {r.id: r for r in rows}


@reports_router.get("/feedback")
async def list_feedback(db: AsyncSession = Depends(get_db), limit: int = 100):
    from .prescriptions_api import patient_labels
    feedbacks = (await db.execute(_scoped(
        select(MedicineFeedback).order_by(MedicineFeedback.created_at.desc()).limit(limit), MedicineFeedback))).scalars().all()
    item_ids = {f.prescription_item_id for f in feedbacks if f.prescription_item_id}
    presc_ids = {f.prescription_id for f in feedbacks if f.prescription_id}
    items = {}
    if item_ids:
        items = {i.id: i for i in (await db.execute(_scoped(
            select(PrescriptionItem).where(PrescriptionItem.id.in_(item_ids)), PrescriptionItem))).scalars().all()}
    prescs = {}
    if presc_ids:
        prescs = {p.id: p for p in (await db.execute(_scoped(
            select(Prescription).where(Prescription.id.in_(presc_ids)), Prescription))).scalars().all()}
    visit_ids = {f.meeting_id for f in feedbacks if f.meeting_id}
    visits = {}
    if visit_ids:
        visits = {m.id: m for m in (await db.execute(_scoped(
            select(DoctorMeetingRequest).where(DoctorMeetingRequest.id.in_(visit_ids)), DoctorMeetingRequest))).scalars().all()}
    people = await patient_labels(db, [p.patient_id for p in prescs.values()] + [m.patient_id for m in visits.values()])
    out = []
    for f in feedbacks:
        presc = prescs.get(f.prescription_id)
        visit = visits.get(f.meeting_id)
        out.append({**f.to_dict(),
                    "medicine": (items.get(f.prescription_item_id).drug_name if items.get(f.prescription_item_id) else None),
                    "doctor_name": presc.doctor_name if presc else (visit.doctor_name if visit else None),
                    "patient": people.get(presc.patient_id) if presc else (people.get(visit.patient_id) if visit else None),
                    "about_visit": bool(visit), "comments": f.notes, "call_id": f.call_id})
    return {"feedback": out, "count": len(out)}


@reports_router.get("/meetings")
async def list_meetings(db: AsyncSession = Depends(get_db), limit: int = 100, source: Optional[str] = None):
    """Doctor meetings. source=inbound lists only the appointments the agent booked for patients who phoned in."""
    from .prescriptions_api import patient_labels
    q = select(DoctorMeetingRequest).order_by(DoctorMeetingRequest.created_at.desc()).limit(limit)
    if source in ("inbound", "followup"):
        q = q.where(DoctorMeetingRequest.source == source)
    rows = (await db.execute(_scoped(q, DoctorMeetingRequest))).scalars().all()
    people = await patient_labels(db, [r.patient_id for r in rows])
    return {"meetings": [{**r.to_dict(), "patient": people.get(r.patient_id), "call_id": r.call_id,
                          "created_at": r.created_at.isoformat() if r.created_at else None} for r in rows],
            "count": len(rows)}


class MeetingUpdate(BaseModel):
    status: str = Field(pattern="^(requested|booked|done|cancelled)$")
    booked_slot: Optional[str] = Field(default=None, max_length=120)
    external_ref: Optional[str] = Field(default=None, max_length=120)


@reports_router.put("/meetings/{meeting_id}")
async def update_meeting(meeting_id: int, body: MeetingUpdate, db: AsyncSession = Depends(get_db)):
    """The clinic confirms (or closes) a doctor-meeting request. Administrators only (writes are denied by default)."""
    row = request_scope().require(await db.get(DoctorMeetingRequest, meeting_id), "Meeting request not found")
    if body.status == "booked" and not (body.booked_slot or row.booked_slot):
        raise HTTPException(status_code=422, detail="Say when the meeting is booked for.")
    was = row.status
    row.status = body.status
    if body.booked_slot is not None:
        row.booked_slot = body.booked_slot.strip() or None
    if body.external_ref is not None:
        row.external_ref = body.external_ref.strip() or None
    feedback_due = None
    if body.status == "done" and was != "done":
        # The visit has taken place: the patient gets a feedback call a couple of days later.
        from .followup_scheduler import schedule_visit_feedback
        row.completed_at = datetime.utcnow()
        task = await schedule_visit_feedback(db, row)
        feedback_due = task.due_at.isoformat() if task is not None else None
    elif was == "done" and body.status != "done":
        from .followup_scheduler import cancel_visit_feedback
        row.completed_at = None
        await cancel_visit_feedback(db, row)
    await db.commit()
    return {**row.to_dict(), "feedback_call_due_at": feedback_due}


# ── Medicine orders taken on inbound calls ─────────────────────────────────────────────────────────
@reports_router.get("/orders")
async def list_orders(db: AsyncSession = Depends(get_db), limit: int = 100):
    from .prescriptions_api import patient_labels
    rows = (await db.execute(_scoped(
        select(MedicineOrder).order_by(MedicineOrder.created_at.desc()).limit(limit), MedicineOrder))).scalars().all()
    people = await patient_labels(db, [r.patient_id for r in rows if r.patient_id])
    return {"orders": [{**r.to_dict(), "patient": people.get(r.patient_id),
                        "delivery_start": r.delivery_start.isoformat() if r.delivery_start else None} for r in rows],
            "count": len(rows)}


class OrderUpdate(BaseModel):
    status: str = Field(pattern="^(scheduled|delivered|cancelled)$")


@reports_router.put("/orders/{order_id}")
async def update_order(order_id: int, body: OrderUpdate, db: AsyncSession = Depends(get_db)):
    """The pharmacy marks an order delivered or cancelled. Administrators only (writes are denied by default)."""
    row = request_scope().require(await db.get(MedicineOrder, order_id), "Order not found")
    if body.status == "delivered" and row.status != "scheduled":
        raise HTTPException(status_code=422, detail="Only an order with a delivery scheduled can be marked delivered.")
    row.status = body.status
    await db.commit()
    return row.to_dict()


@reports_router.get("/settings")
async def patient_care_settings(db: AsyncSession = Depends(get_db)):
    """What the pages need to show times the way this company reads them."""
    tenant_id = request_scope().tenant_id
    tenant = await db.get(Tenant, tenant_id) if tenant_id is not None else None
    return {"timezone": (tenant.timezone if tenant and tenant.timezone else "Asia/Kolkata")}
