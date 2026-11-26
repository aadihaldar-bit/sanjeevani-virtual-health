"""A patient's record, read by the company's own systems with their API key.

``GET /api/v1/patients/record?external_id=...&phone=...`` (Authorization: Bearer stl_outbound_...) returns what the voice agent
knows about one patient of the key's company: their details, prescriptions and medicines, the follow-up calls booked and made,
recent calls with their summaries, doctor appointments, pharmacy orders and medicine feedback. Read-only.

Only a key that belongs to a company can read, and only that company's rows are searched (the filter is written here, not left to
the console's request scope, which an API request does not have). A key with no company is refused rather than reading across
companies."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db
from .models import (APIClient, Call, DoctorMeetingRequest, FollowUpTask, MedicineFeedback, MedicineOrder, Patient,
                     Prescription, PrescriptionItem)
from .outbound_api_auth import verify_api_key

router = APIRouter(prefix="/api/v1/patients", tags=["patient-record"])

RECENT_CALLS = 10
RECENT_ROWS = 20


def _iso(d: Optional[datetime]) -> Optional[str]:
    return d.isoformat() if d else None


async def find_patient(db: AsyncSession, tenant_id: int, external_id: Optional[str], phone: Optional[str]) -> Optional[Patient]:
    """The company's patient with this id (the hospital's MRN), else with this phone number."""
    if external_id:
        p = (await db.execute(select(Patient).where(Patient.tenant_id == tenant_id, Patient.external_id == external_id))).scalars().first()
        if p is not None:
            return p
    if phone:
        return (await db.execute(select(Patient).where(Patient.tenant_id == tenant_id, Patient.phone_e164 == phone)
                                 .order_by(Patient.id.desc()))).scalars().first()
    return None


async def patient_record(db: AsyncSession, tenant_id: int, external_id: Optional[str] = None,
                         phone: Optional[str] = None) -> Optional[dict]:
    patient = await find_patient(db, tenant_id, external_id, phone)
    phone = (patient.phone_e164 if patient else None) or phone
    if patient is None and not phone:
        return None

    # Appointments and orders made on an inbound call may only carry the caller's number, so match on either.
    def mine(model, patient_col, phone_col):
        conds = []
        if patient is not None:
            conds.append(patient_col == patient.id)
        if phone:
            conds.append(phone_col == phone)
        return select(model).where(model.tenant_id == tenant_id, or_(*conds))

    meetings = (await db.execute(mine(DoctorMeetingRequest, DoctorMeetingRequest.patient_id, DoctorMeetingRequest.phone)
                                 .order_by(DoctorMeetingRequest.created_at.desc()).limit(RECENT_ROWS))).scalars().all()
    orders = (await db.execute(mine(MedicineOrder, MedicineOrder.patient_id, MedicineOrder.contact_phone)
                               .order_by(MedicineOrder.created_at.desc()).limit(RECENT_ROWS))).scalars().all()
    calls = []
    if phone:
        calls = (await db.execute(select(Call).where(Call.tenant_id == tenant_id,
                                                     or_(Call.called_number == phone, Call.caller_number == phone))
                                  .order_by(Call.started_at.desc()).limit(RECENT_CALLS))).scalars().all()
    if patient is None and not (meetings or orders or calls):
        return None

    prescriptions, tasks, feedback = [], [], []
    if patient is not None:
        rx_rows = (await db.execute(select(Prescription).where(Prescription.tenant_id == tenant_id,
                                                               Prescription.patient_id == patient.id)
                                    .order_by(Prescription.issued_at.desc()).limit(RECENT_ROWS))).scalars().all()
        rx_ids = [r.id for r in rx_rows]
        items = (await db.execute(select(PrescriptionItem).where(PrescriptionItem.tenant_id == tenant_id,
                                                                 PrescriptionItem.prescription_id.in_(rx_ids)))).scalars().all() if rx_ids else []
        for r in rx_rows:
            prescriptions.append({
                "id": r.id, "external_id": r.external_id, "doctor_name": r.doctor_name, "facility": r.facility,
                "issued_at": _iso(r.issued_at), "status": r.status,
                "items": [{"drug_name": i.drug_name, "strength": i.strength, "form": i.form, "dose_text": i.dose_text,
                           "timing": i.timing, "duration_days": i.duration_days, "end_date": _iso(i.end_date), "status": i.status}
                          for i in items if i.prescription_id == r.id],
            })
        tasks = [{"kind": t.kind, "due_at": _iso(t.due_at), "status": t.status, "attempts": t.attempts,
                  "prescription_id": t.prescription_id}
                 for t in (await db.execute(select(FollowUpTask).where(FollowUpTask.tenant_id == tenant_id,
                                                                       FollowUpTask.patient_id == patient.id)
                                            .order_by(FollowUpTask.due_at.desc()).limit(RECENT_ROWS))).scalars().all()]
        if rx_ids:
            feedback = [{"taken": f.taken, "helped": f.helped, "satisfaction": f.satisfaction,
                         "side_effects": f.side_effects_text, "wants_doctor": f.wants_doctor,
                         "refill_requested": f.refill_requested, "created_at": _iso(f.created_at)}
                        for f in (await db.execute(select(MedicineFeedback).where(MedicineFeedback.tenant_id == tenant_id,
                                                                                  MedicineFeedback.prescription_id.in_(rx_ids))
                                                   .order_by(MedicineFeedback.created_at.desc()).limit(RECENT_ROWS))).scalars().all()]

    return {
        "patient": {
            "found": patient is not None,
            "external_id": patient.external_id if patient else external_id,
            "name": " ".join(x for x in (patient.first_name, patient.last_name) if x) if patient else None,
            "guardian_name": patient.guardian_name if patient else None,
            "phone": phone,
            "preferred_language": patient.preferred_language if patient else None,
            "consent_calls": patient.consent_calls if patient else None,
            "do_not_call": patient.do_not_call if patient else None,
        },
        "prescriptions": prescriptions,
        "follow_up_calls": tasks,
        "appointments": [{"doctor_name": m.doctor_name, "specialty": m.specialty, "reason": m.reason,
                          "slot_start": _iso(m.slot_start), "booked_slot": m.booked_slot, "status": m.status,
                          "source": m.source, "fee_amount": m.fee_amount, "copay_amount": m.copay_amount,
                          "payment_status": m.payment_status, "created_at": _iso(m.created_at)} for m in meetings],
        "orders": [{"reference": o.reference, "status": o.status, "items": o.items_json, "requested": o.requested_text,
                    "amount": o.amount, "payment_status": o.payment_status, "delivery_slot": o.delivery_slot,
                    "delivery_start": _iso(o.delivery_start), "created_at": _iso(o.created_at)} for o in orders],
        "calls": [{"id": c.id, "direction": "inbound" if c.caller_number == phone and c.called_number != phone else "outbound",
                   "started_at": _iso(c.started_at), "duration_seconds": c.duration_seconds, "status": c.status,
                   "summary": c.summary, "sentiment": c.sentiment, "agent": c.current_agent} for c in calls],
        "feedback": feedback,
    }


@router.get("/record")
async def get_patient_record(external_id: Optional[str] = None, phone: Optional[str] = None,
                             client: APIClient = Depends(verify_api_key), db: AsyncSession = Depends(get_db)):
    tenant_id = getattr(client, "tenant_id", None)
    if tenant_id is None:
        raise HTTPException(403, "this API key does not belong to a company, so it cannot read patient records")
    external_id = (external_id or "").strip() or None
    phone = (phone or "").strip() or None
    if not external_id and not phone:
        raise HTTPException(422, "give external_id or phone")
    record = await patient_record(db, tenant_id, external_id, phone)
    if record is None:
        raise HTTPException(404, "no patient with that id or phone number")
    return record


class AppointmentCreateIn(BaseModel):
    external_id: Optional[str] = None
    phone: Optional[str] = None
    patient_name: Optional[str] = None
    doctor_name: str
    specialty: Optional[str] = None
    booked_slot: Optional[str] = None
    slot_start: Optional[datetime] = None
    reason: Optional[str] = "Consultation"
    fee_amount: Optional[float] = 400.0
    copay_amount: Optional[float] = 400.0
    insurer: Optional[str] = None


@router.post("/appointments")
async def create_appointment(body: AppointmentCreateIn, client: APIClient = Depends(verify_api_key),
                             db: AsyncSession = Depends(get_db)):
    tenant_id = getattr(client, "tenant_id", None)
    if tenant_id is None:
        raise HTTPException(403, "this API key does not belong to a company")
    external_id = (body.external_id or "").strip() or None
    phone = (body.phone or "").strip() or None
    patient = await find_patient(db, tenant_id, external_id, phone)
    if patient is None:
        if not phone:
            raise HTTPException(422, "give at least a phone number or valid patient ID")
        first, _, last = (body.patient_name or "Patient").strip().partition(" ")
        patient = Patient(tenant_id=tenant_id, external_id=external_id, phone_e164=phone,
                          first_name=first, last_name=last or None, preferred_language="en",
                          consent_calls=True)
        db.add(patient)
        await db.flush()
    else:
        if external_id and not patient.external_id:
            patient.external_id = external_id
        if phone and not patient.phone_e164:
            patient.phone_e164 = phone

    meeting = DoctorMeetingRequest(
        tenant_id=tenant_id,
        patient_id=patient.id,
        doctor_name=body.doctor_name,
        specialty=body.specialty,
        booked_slot=body.booked_slot or "Next available slot",
        slot_start=body.slot_start,
        reason=body.reason or "Doctor consultation",
        source="inbound",
        status="booked",
        patient_name=body.patient_name or patient.first_name,
        phone=patient.phone_e164,
        fee_amount=body.fee_amount,
        copay_amount=body.copay_amount,
        insurer=body.insurer,
        payment_status="to_pay_at_visit",
    )
    db.add(meeting)
    await db.flush()
    meeting.external_ref = f"BJ-{meeting.id:03d}"
    await db.commit()
    return {
        "status": "booked",
        "appointment_id": meeting.id,
        "external_ref": meeting.external_ref,
        "doctor_name": meeting.doctor_name,
        "booked_slot": meeting.booked_slot,
        "patient": {
            "id": patient.id,
            "external_id": patient.external_id,
            "name": patient.first_name,
            "phone": patient.phone_e164,
        }
    }


class PatientUpsertIn(BaseModel):
    external_id: Optional[str] = None
    phone_e164: str
    first_name: str
    last_name: Optional[str] = None
    identity_dob: Optional[str] = None
    preferred_language: Optional[str] = "en"
    consent_calls: Optional[bool] = True


@router.get("", status_code=200)
@router.get("/", include_in_schema=False)
async def list_company_patients(client: APIClient = Depends(verify_api_key), db: AsyncSession = Depends(get_db)):
    tenant_id = getattr(client, "tenant_id", None)
    if tenant_id is None:
        raise HTTPException(403, "API client has no company/tenant assigned")
    rows = (await db.execute(
        select(Patient)
        .where(Patient.tenant_id == tenant_id)
        .order_by(Patient.first_name, Patient.last_name)
    )).scalars().all()

    return {
        "patients": [
            {
                "id": p.id,
                "external_id": p.external_id,
                "name": " ".join(x for x in (p.first_name, p.last_name) if x) or "Unnamed",
                "first_name": p.first_name,
                "last_name": p.last_name,
                "guardian_name": p.guardian_name,
                "phone_e164": p.phone_e164,
                "identity_dob": p.identity_dob,
                "preferred_language": p.preferred_language,
                "consent_calls": p.consent_calls,
                "do_not_call": p.do_not_call,
            }
            for p in rows
        ],
        "count": len(rows),
    }


@router.post("/upsert", status_code=200)
async def upsert_company_patient(body: PatientUpsertIn, client: APIClient = Depends(verify_api_key), db: AsyncSession = Depends(get_db)):
    tenant_id = getattr(client, "tenant_id", None)
    if tenant_id is None:
        raise HTTPException(403, "API client has no company/tenant assigned")

    patient = await find_patient(db, tenant_id, body.external_id, body.phone_e164)
    if patient is None:
        patient = Patient(
            tenant_id=tenant_id,
            external_id=body.external_id,
            first_name=body.first_name,
            last_name=body.last_name,
            phone_e164=body.phone_e164,
            identity_dob=body.identity_dob,
            preferred_language=body.preferred_language or "en",
            consent_calls=body.consent_calls if body.consent_calls is not None else True,
        )
        db.add(patient)
    else:
        if body.external_id and not patient.external_id:
            patient.external_id = body.external_id
        if body.first_name:
            patient.first_name = body.first_name
        if body.last_name:
            patient.last_name = body.last_name
        if body.identity_dob:
            patient.identity_dob = body.identity_dob
        if body.phone_e164:
            patient.phone_e164 = body.phone_e164
        if body.consent_calls is not None:
            patient.consent_calls = body.consent_calls
        if body.preferred_language:
            patient.preferred_language = body.preferred_language

    await db.commit()
    await db.refresh(patient)
    return {
        "status": "success",
        "patient": {
            "id": patient.id,
            "external_id": patient.external_id,
            "name": " ".join(x for x in (patient.first_name, patient.last_name) if x) or "Unnamed",
            "phone_e164": patient.phone_e164,
            "identity_dob": patient.identity_dob,
        }
    }


