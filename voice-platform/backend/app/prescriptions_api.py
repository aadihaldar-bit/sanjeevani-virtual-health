"""Sanjeevani prescription ingestion + follow-up console API.

Two surfaces:

* ``POST /api/v1/prescriptions`` — machine-to-machine push from the hospital system, authenticated
  with an API client key exactly like ``outbound_api.py`` (tenant taken from the key's client). It
  upserts the patient, creates the Prescription + items (computing each item's ``end_date``), is
  idempotent on ``prescription.external_id``, and schedules the follow-up tasks.
* ``/api/sanjeevani/...`` — the tenant console (Firebase + tenant scope, mounted behind ``_data_dep``
  in ``main.py``). Upload page, listings, and run/cancel controls for follow-up tasks. Writes such as
  run/cancel are refused to non-admin roles by ``tenancy.role_denial`` (applied by ``tenant_scope``).
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .database import get_db
from .models import APIClient, FollowUpTask, Patient, Prescription, PrescriptionItem
from .outbound_api_auth import verify_api_key
from .followup_scheduler import create_followup_tasks, place_followup_call, run_due_tasks
from .tenancy import request_scope

logger = logging.getLogger(__name__)

v1_router = APIRouter(prefix="/api/v1", tags=["sanjeevani-prescriptions"])
console_router = APIRouter(prefix="/api/sanjeevani", tags=["sanjeevani-console"])


# ── Schemas ──────────────────────────────────────────────────────────────────────────────────
class PatientIn(BaseModel):
    username: Optional[str] = None          # the patient's id in the directory (same as external_id)
    name: Optional[str] = None              # "First Last": looked up in the patient directory when no phone is given
    external_id: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    guardian_name: Optional[str] = None
    phone_e164: Optional[str] = None
    identity_dob: Optional[str] = None
    preferred_language: Optional[str] = "en"
    do_not_call: Optional[bool] = None
    consent_calls: Optional[bool] = None


class ItemIn(BaseModel):
    drug_name: Optional[str] = None
    strength: Optional[str] = None
    form: Optional[str] = None
    dose_text: Optional[str] = None
    frequency_per_day: Optional[int] = None
    timing: Optional[str] = None
    start_date: Optional[datetime] = None
    duration_days: Optional[int] = None
    refills: Optional[int] = 0
    special_instructions: Optional[str] = None
    plain_summary_en: Optional[str] = None
    plain_summary_ar: Optional[str] = None
    warnings: Optional[str] = None
    status: Optional[str] = "confirmed"
    open_question: Optional[str] = None


class PrescriptionIn(BaseModel):
    external_id: Optional[str] = None
    doctor_name: Optional[str] = None
    doctor_external_id: Optional[str] = None
    facility: Optional[str] = None
    course_days: Optional[int] = None
    extra_instruction: Optional[str] = None
    issued_at: Optional[datetime] = None


class PrescriptionPushRequest(BaseModel):
    patient: PatientIn
    prescription: PrescriptionIn
    items: List[ItemIn] = Field(default_factory=list)


# ── Core ingestion (pure, tenant_id explicit — reused by the API and the upload page) ─────────
def _compute_end_date(start: Optional[datetime], duration_days: Optional[int]) -> Optional[datetime]:
    if start is None or duration_days is None:
        return None
    return start + timedelta(days=duration_days)


class PatientNotFound(Exception):
    """A prescription named a patient who is not in the directory and gave no phone number to call."""


def resolve_confirmed_status(status: Optional[str], dose_text: Optional[str]) -> str:
    """The safety rule for confirming a medicine: a medicine may only be ``confirmed`` if it has a
    dose. Asking to confirm one with no dose downgrades it to ``needs_clarification`` so the agent
    never speaks a dose that no one supplied. Used by the confirm endpoint and covered by tests."""
    status = (status or "confirmed").lower()
    if status == "confirmed" and not (dose_text or "").strip():
        return "needs_clarification"
    return status


def _norm_name(first: Optional[str], last: Optional[str] = None) -> str:
    return " ".join(f"{first or ''} {last or ''}".lower().split())


async def _upsert_patient(db: AsyncSession, tenant_id: Optional[int], p: PatientIn) -> Patient:
    """Find the patient in this company's directory (by username, phone or name) and use what is stored there: the phone
    to call and the date of birth for the identity check. A new patient needs a phone number."""
    if p.name and not p.first_name:
        parts = p.name.strip().split(None, 1)
        p.first_name, p.last_name = parts[0], (parts[1] if len(parts) > 1 else p.last_name)
    username = p.username or p.external_id
    if username and not p.external_id:
        p.external_id = username
    patient = None
    if username:
        patient = (await db.execute(select(Patient).where(
            Patient.tenant_id == tenant_id, Patient.external_id == username))).scalars().first()
    if patient is None and p.phone_e164:
        patient = (await db.execute(select(Patient).where(
            Patient.tenant_id == tenant_id, Patient.phone_e164 == p.phone_e164))).scalars().first()
    if patient is None and p.first_name:
        wanted = _norm_name(p.first_name, p.last_name)
        everyone = (await db.execute(select(Patient).where(Patient.tenant_id == tenant_id))).scalars().all()
        patient = next((x for x in everyone if _norm_name(x.first_name, x.last_name) == wanted), None)
    if patient is None:
        if not p.phone_e164:
            who = username or p.name or _norm_name(p.first_name, p.last_name) or "that patient"
            raise PatientNotFound(f"{who} is not in the patient directory, and no phone number was given.")
        patient = Patient(tenant_id=tenant_id)
        db.add(patient)
    for field in ("external_id", "first_name", "last_name", "guardian_name", "phone_e164",
                  "identity_dob", "preferred_language"):
        val = getattr(p, field)
        if val is not None:
            setattr(patient, field, val)
    if p.do_not_call is not None:
        patient.do_not_call = p.do_not_call
    if p.consent_calls is not None:
        patient.consent_calls = p.consent_calls
    await db.flush()
    return patient


async def ingest_prescription(db: AsyncSession, tenant_id: Optional[int],
                              payload: PrescriptionPushRequest, schedule: bool = True,
                              draft: Optional[bool] = None) -> dict:
    """Upsert patient, create prescription+items (idempotent on external_id), schedule tasks.

    ``draft`` controls whether the prescription is held for review (``status='draft'``, items forced
    to ``'draft'``); it defaults to ``not schedule`` for backward compatibility. ``schedule`` controls
    whether follow-up calls are created. The two are separate so a parsed PDF can be made **visible but
    call-less** (``schedule=False, draft=False``) when the review layer is turned off for testing,
    while the normal PDF path stays ``schedule=False, draft=True`` (held for a pharmacist to confirm,
    the only path that then schedules calls — ``/prescriptions/{id}/confirm``).
    """
    if draft is None:
        draft = not schedule
    try:
        patient = await _upsert_patient(db, tenant_id, payload.patient)
    except PatientNotFound as exc:
        return {"status": "patient_not_found", "detail": str(exc), "items_created": 0, "tasks_created": 0}

    existing = None
    if payload.prescription.external_id:
        existing = (await db.execute(select(Prescription).where(
            Prescription.tenant_id == tenant_id,
            Prescription.external_id == payload.prescription.external_id))).scalars().first()
    if existing is not None:
        await db.commit()
        return {"status": "exists", "patient_id": patient.id, "prescription_id": existing.id,
                "items_created": 0, "tasks_created": 0}

    presc = Prescription(
        tenant_id=tenant_id, patient_id=patient.id,
        external_id=payload.prescription.external_id, doctor_name=payload.prescription.doctor_name,
        doctor_external_id=payload.prescription.doctor_external_id,
        facility=payload.prescription.facility, course_days=payload.prescription.course_days,
        extra_instruction=payload.prescription.extra_instruction,
        issued_at=payload.prescription.issued_at or datetime.utcnow(),
        status="draft" if draft else "issued",
    )
    db.add(presc)
    await db.flush()

    items: List[PrescriptionItem] = []
    for it in payload.items:
        it.start_date = it.start_date or presc.issued_at            # a course starts when the prescription is issued
        item = PrescriptionItem(
            tenant_id=tenant_id, prescription_id=presc.id, drug_name=it.drug_name,
            strength=it.strength, form=it.form, dose_text=it.dose_text,
            frequency_per_day=it.frequency_per_day, timing=it.timing, start_date=it.start_date,
            duration_days=it.duration_days, end_date=_compute_end_date(it.start_date, it.duration_days),
            refills=it.refills or 0, special_instructions=it.special_instructions,
            plain_summary_en=it.plain_summary_en, plain_summary_ar=it.plain_summary_ar,
            warnings=it.warnings,
            status="draft" if draft else (it.status or "confirmed"),
            open_question=it.open_question,
        )
        db.add(item)
        items.append(item)
    await db.flush()

    if not schedule:
        await db.commit()
        return {"status": "draft" if draft else "created", "patient_id": patient.id,
                "prescription_id": presc.id, "items_created": len(items), "tasks_created": 0}

    tasks = await create_followup_tasks(db, patient, presc, items)
    await db.commit()
    return {"status": "created", "patient_id": patient.id, "prescription_id": presc.id,
            "items_created": len(items), "tasks_created": len(tasks),
            "feedback_tasks": sum(1 for t in tasks if t.kind == "feedback")}


# ── v1 machine push (API client key auth) ─────────────────────────────────────────────────────
@v1_router.post("/prescriptions", status_code=201)
async def push_prescription(payload: PrescriptionPushRequest,
                            client: APIClient = Depends(verify_api_key),
                            db: AsyncSession = Depends(get_db)):
    logger.info("[Sanjeevani] prescription push from client '%s' (tenant %s)",
                client.client_name, getattr(client, "tenant_id", None))
    result = await ingest_prescription(db, getattr(client, "tenant_id", None), payload)
    if result.get("status") == "patient_not_found":
        raise HTTPException(status_code=422, detail=result["detail"])
    return result


# ── Console: CSV/JSON upload page ─────────────────────────────────────────────────────────────
def _parse_csv_rows(text: str) -> List[PrescriptionPushRequest]:
    """Group flat CSV rows into one push per (patient, prescription_external_id)."""
    reader = csv.DictReader(io.StringIO(text))
    groups: dict = {}
    order: List[str] = []
    for raw in reader:
        row = {(k or "").strip(): (v.strip() if isinstance(v, str) else v) for k, v in raw.items()}
        presc_ext = row.get("prescription_external_id") or row.get("prescription_id") or ""
        pat_ext = row.get("username") or row.get("patient_external_id") or row.get("external_id") or ""
        key = f"{pat_ext}|{presc_ext}"
        if key not in groups:
            order.append(key)
            groups[key] = {
                "patient": PatientIn(
                    external_id=pat_ext or None, name=row.get("patient_name") or row.get("name") or None, first_name=row.get("first_name") or None,
                    last_name=row.get("last_name") or None, guardian_name=row.get("guardian_name") or None,
                    phone_e164=row.get("phone_e164") or row.get("phone") or None,
                    identity_dob=row.get("identity_dob") or row.get("dob") or None,
                    preferred_language=row.get("preferred_language") or "en"),
                "prescription": PrescriptionIn(
                    external_id=presc_ext or None, doctor_name=row.get("doctor_name") or None,
                    facility=row.get("facility") or None),
                "items": [],
            }

        def _int(v):
            try:
                return int(v) if str(v).strip() else None
            except (TypeError, ValueError):
                return None

        def _dt(v):
            v = (v or "").strip()
            if not v:
                return None
            for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y"):
                try:
                    return datetime.strptime(v, fmt)
                except ValueError:
                    continue
            return None

        if row.get("drug_name"):
            groups[key]["items"].append(ItemIn(
                drug_name=row.get("drug_name"), strength=row.get("strength") or None,
                form=row.get("form") or None, dose_text=row.get("dose_text") or None,
                frequency_per_day=_int(row.get("frequency_per_day")), timing=row.get("timing") or None,
                start_date=_dt(row.get("start_date")), duration_days=_int(row.get("duration_days")),
                plain_summary_en=row.get("plain_summary_en") or None,
                plain_summary_ar=row.get("plain_summary_ar") or None,
                status=row.get("status") or "confirmed", open_question=row.get("open_question") or None))
    return [PrescriptionPushRequest(**groups[k]) for k in order]


@console_router.post("/prescriptions/upload")
async def upload_prescriptions(file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    scope = request_scope()
    tenant_id = scope.for_new_row()
    raw = (await file.read()).decode("utf-8", errors="replace")
    name = (file.filename or "").lower()
    try:
        if name.endswith(".json") or raw.lstrip().startswith(("{", "[")):
            data = json.loads(raw)
            records = data if isinstance(data, list) else [data]
            pushes = [PrescriptionPushRequest(**r) for r in records]
        else:
            pushes = _parse_csv_rows(raw)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse upload: {exc}")

    results = []
    for push in pushes:
        results.append(await ingest_prescription(db, tenant_id, push))
    return {"uploaded": len(results), "results": results}


# ── Console: PDF prescription reader → draft for review (admin only; writes denied to others) ────
@console_router.post("/prescriptions/parse-pdf")
async def parse_pdf_prescription(file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    """Read a *digital* PDF prescription, create a DRAFT prescription for review, and return it.

    No call is scheduled here. The draft (and every medicine on it) is saved with ``status='draft'``;
    a machine-read dose is never spoken to a patient until a person confirms it through
    ``/prescriptions/{id}/confirm``. Scanned/handwritten PDFs (no text layer) are rejected, not guessed.
    """
    from .prescription_pdf import PdfParseError, extract_pdf_text, extract_prescription_fields
    scope = request_scope()
    tenant_id = scope.for_new_row()
    raw = await file.read()
    name = (file.filename or "").lower()
    if not (name.endswith(".pdf") or raw[:5] == b"%PDF-"):
        raise HTTPException(status_code=400, detail="Please upload a PDF file.")
    try:
        text = extract_pdf_text(raw)
        fields = extract_prescription_fields(text)
    except PdfParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    try:
        push = PrescriptionPushRequest(
            patient=PatientIn(**fields["patient"]),
            prescription=PrescriptionIn(**fields["prescription"]),
            items=[ItemIn(**it) for it in fields["items"]],
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"The prescription could not be read: {exc}")

    # A parsed PDF often has no phone yet (it is not in the directory). Keep it anyway so the parse can
    # be seen; the reviewer links a patient (name/phone) before confirming.
    if not (push.patient.phone_e164 or push.patient.first_name or push.patient.name):
        raise HTTPException(status_code=422, detail="No patient name could be read from the prescription.")
    if not push.patient.phone_e164:
        push.patient.phone_e164 = f"draft-pending-{datetime.utcnow().timestamp():.0f}"

    # Review layer: ON by default (parsed PDF is held as a draft for a pharmacist to confirm before any
    # call). Set SANJEEVANI_PDF_SKIP_REVIEW=true to bypass it — the parse is saved straight away as a
    # visible prescription (no calls are scheduled either way here) so the raw extraction can be judged.
    skip_review = os.getenv("SANJEEVANI_PDF_SKIP_REVIEW", "false").strip().lower() in ("1", "true", "yes", "on")

    result = await ingest_prescription(db, tenant_id, push, schedule=False, draft=not skip_review)
    if result.get("status") == "patient_not_found":
        raise HTTPException(status_code=422, detail=result["detail"])

    presc = (await db.execute(select(Prescription).options(selectinload(Prescription.items))
                              .where(Prescription.id == result["prescription_id"]))).scalars().first()
    low_confidence = sum(1 for it in (presc.items if presc else []) if it.status == "needs_clarification")
    return {
        "status": "created" if skip_review else "draft",
        "review_skipped": skip_review,
        "prescription": presc.to_dict() if presc else None,
        "needs_review": low_confidence,
        "warning": ("Read from the PDF by the assistant (review is OFF). Doses are shown exactly as read; "
                    "check them before any real use." if skip_review else
                    "Read from the PDF by the assistant. Check every medicine and dose before confirming; "
                    "doses marked 'to confirm' had no clear value on the prescription."),
    }


# ── Console: confirm a draft → schedule the calls (admin only) ───────────────────────────────────
class ConfirmItem(BaseModel):
    id: int
    drug_name: Optional[str] = None
    strength: Optional[str] = None
    form: Optional[str] = None
    dose_text: Optional[str] = None
    frequency_per_day: Optional[int] = None
    timing: Optional[str] = None
    duration_days: Optional[int] = None
    warnings: Optional[str] = None
    plain_summary_en: Optional[str] = None
    plain_summary_ar: Optional[str] = None
    status: Optional[str] = "confirmed"               # confirmed | needs_clarification
    open_question: Optional[str] = None


class ConfirmRequest(BaseModel):
    patient: Optional[PatientIn] = None               # reviewer can set/correct the phone, dob, name, language
    doctor_name: Optional[str] = None
    facility: Optional[str] = None
    extra_instruction: Optional[str] = None
    items: List[ConfirmItem] = Field(default_factory=list)


@console_router.post("/prescriptions/{prescription_id}/confirm")
async def confirm_prescription(prescription_id: int, body: ConfirmRequest, db: AsyncSession = Depends(get_db)):
    """Apply a reviewer's corrections to a DRAFT prescription and schedule its follow-up calls.

    This is the only place a parsed PDF turns into real calls. A medicine kept as ``confirmed`` must
    have a dose; without one it is forced to ``needs_clarification`` so the agent never states a guessed
    dose. Scheduling only happens here, after a person has signed off.
    """
    scope = request_scope()
    presc = scope.require(
        (await db.execute(select(Prescription).options(selectinload(Prescription.items))
                          .where(Prescription.id == prescription_id))).scalars().first(),
        "Prescription not found")
    if presc.status != "draft":
        raise HTTPException(status_code=409, detail=f"This prescription is already {presc.status}; it cannot be confirmed again.")

    patient = scope.require(await db.get(Patient, presc.patient_id), "Patient not found")

    # Reviewer may fix the patient (a PDF rarely has a phone). A real phone is required before calling.
    if body.patient is not None:
        await _upsert_patient(db, presc.tenant_id, body.patient)
        await db.refresh(patient)
    if not patient.phone_e164 or patient.phone_e164.startswith("draft-pending-"):
        raise HTTPException(status_code=422, detail="Add a phone number for the patient before confirming (under Patients, or in this form).")
    if not patient.identity_dob:
        raise HTTPException(status_code=422, detail="Add the patient's date of birth (used for the identity check) before confirming.")

    if body.doctor_name is not None:
        presc.doctor_name = body.doctor_name.strip() or None
    if body.facility is not None:
        presc.facility = body.facility.strip() or None
    if body.extra_instruction is not None:
        presc.extra_instruction = body.extra_instruction.strip() or None

    by_id = {it.id: it for it in presc.items}
    confirmed_any = False
    for edit in body.items:
        item = by_id.get(edit.id)
        if item is None:
            continue
        for field in ("drug_name", "strength", "form", "dose_text", "timing", "warnings",
                      "plain_summary_en", "plain_summary_ar", "open_question"):
            val = getattr(edit, field)
            if val is not None:
                setattr(item, field, (val.strip() or None) if isinstance(val, str) else val)
        if edit.frequency_per_day is not None:
            item.frequency_per_day = edit.frequency_per_day
        if edit.duration_days is not None:
            item.duration_days = edit.duration_days
        # Safety: a medicine with no dose can never be "confirmed" — it stays for the pharmacist.
        status = resolve_confirmed_status(edit.status, item.dose_text)
        if status == "needs_clarification" and (edit.status or "confirmed").lower() == "confirmed" and not item.open_question:
            item.open_question = "The dose is still missing. A pharmacist must confirm it."
        item.status = status
        item.end_date = _compute_end_date(item.start_date, item.duration_days)
        if status == "confirmed":
            confirmed_any = True

    if not confirmed_any:
        raise HTTPException(status_code=422, detail="At least one medicine must have a confirmed dose before scheduling calls.")

    presc.status = "issued"
    await db.flush()
    tasks = await create_followup_tasks(db, patient, presc, presc.items)
    await db.commit()
    return {"status": "issued", "prescription_id": presc.id, "tasks_created": len(tasks),
            "feedback_tasks": sum(1 for t in tasks if t.kind == "feedback")}


# ── Console: listings ──────────────────────────────────────────────────────────────────────────
def mask_phone(number: Optional[str]) -> Optional[str]:
    """+971500001234 -> +971 ••• 1234: enough to tell two patients apart, not enough to call them."""
    digits = "".join(ch for ch in (number or "") if ch.isdigit())
    if len(digits) < 6:
        return number or None
    return f"+{digits[:3]} ••• {digits[-4:]}"


async def patient_labels(db: AsyncSession, ids) -> dict:
    """patient id -> {name, contact, phone} for the pages' tables."""
    ids = {i for i in ids if i}
    if not ids:
        return {}
    rows = (await db.execute(select(Patient).where(Patient.id.in_(ids)))).scalars().all()
    out = {}
    for p in rows:
        name = " ".join(x for x in (p.first_name, p.last_name) if x) or "Unnamed patient"
        out[p.id] = {"name": name, "contact": p.guardian_name, "phone": mask_phone(p.phone_e164),
                     "language": p.preferred_language, "do_not_call": bool(p.do_not_call)}
    return out


@console_router.get("/prescriptions")
async def list_prescriptions(db: AsyncSession = Depends(get_db), limit: int = 100):
    rows = (await db.execute(
        select(Prescription).options(selectinload(Prescription.items))      # items are read lazily otherwise, which fails in async code
        .order_by(Prescription.created_at.desc()).limit(limit))).scalars().all()
    people = await patient_labels(db, [p.patient_id for p in rows])
    return {"prescriptions": [{**p.to_dict(), "patient": people.get(p.patient_id),
                               "issued_at": p.issued_at.isoformat() if p.issued_at else None} for p in rows],
            "count": len(rows)}


# ── The patient directory: who the agent may call, and what to check them against ────────────────────────────
class PatientBody(BaseModel):
    username: Optional[str] = Field(default=None, max_length=120)
    first_name: str = Field(min_length=1, max_length=120)
    last_name: Optional[str] = Field(default=None, max_length=120)
    guardian_name: Optional[str] = Field(default=None, max_length=120)
    phone_e164: str = Field(min_length=6, max_length=32)
    identity_dob: str = Field(min_length=6, max_length=32)
    preferred_language: Optional[str] = Field(default="en", max_length=16)


@console_router.get("/patients")
async def list_patients(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Patient).order_by(Patient.first_name, Patient.last_name))).scalars().all()
    return {"patients": [{"id": p.id, "username": p.external_id,
                          "name": " ".join(x for x in (p.first_name, p.last_name) if x) or "Unnamed",
                          "guardian_name": p.guardian_name, "phone": mask_phone(p.phone_e164),
                          "language": p.preferred_language, "do_not_call": bool(p.do_not_call),
                          "has_dob": bool(p.identity_dob)} for p in rows], "count": len(rows)}


@console_router.post("/patients", status_code=201)
async def add_patient(body: PatientBody, db: AsyncSession = Depends(get_db)):
    """Add (or update) a patient in the directory. Administrators only (writes are denied by default)."""
    tenant_id = request_scope().for_new_row()
    from .transfer import clean_number
    try:
        phone = clean_number(body.phone_e164)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if not phone:
        raise HTTPException(status_code=422, detail="A phone number is needed.")
    p = PatientIn(username=body.username, first_name=body.first_name, last_name=body.last_name, guardian_name=body.guardian_name,
                  phone_e164=phone, identity_dob=body.identity_dob, preferred_language=body.preferred_language or "en")
    patient = await _upsert_patient(db, tenant_id, p)
    await db.commit()
    return {"id": patient.id, "name": " ".join(x for x in (patient.first_name, patient.last_name) if x), "phone": mask_phone(patient.phone_e164)}


@console_router.get("/followups")
async def list_followups(db: AsyncSession = Depends(get_db), status: Optional[str] = None,
                         limit: int = 200):
    stmt = select(FollowUpTask).order_by(FollowUpTask.due_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(FollowUpTask.status == status)
    rows = (await db.execute(stmt)).scalars().all()
    people = await patient_labels(db, [t.patient_id for t in rows])
    return {"followups": [{**t.to_dict(), "patient": people.get(t.patient_id), "last_call_id": t.last_call_id,
                           "outcome": t.outcome_json or {}} for t in rows], "count": len(rows)}


# ── Console: run / cancel (admin only via role_denial on tenant_scope) ──────────────────────────
@console_router.post("/followups/{task_id}/run")
async def run_followup(task_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    scope = request_scope()
    task = scope.require(await db.get(FollowUpTask, task_id), "Follow-up task not found")
    if task.status in ("cancelled", "done"):
        raise HTTPException(status_code=409, detail=f"Task is already {task.status}.")
    task.status = "calling"
    task.locked_at = datetime.utcnow()
    task.attempts = (task.attempts or 0) + 1
    await db.commit()
    try:
        call_id = await place_followup_call(db, task)
    except Exception as exc:
        logger.warning("[Sanjeevani] manual run of task %s failed: %s", task_id, exc)
        task.status = "no_answer"
        await db.commit()
        raise HTTPException(status_code=502, detail="Could not place the call just now.")
    if call_id is not None:
        task.last_call_id = call_id
        await db.commit()
    return {"status": "calling", "task_id": task_id, "call_id": call_id}


@console_router.post("/followups/{task_id}/cancel")
async def cancel_followup(task_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    scope = request_scope()
    task = scope.require(await db.get(FollowUpTask, task_id), "Follow-up task not found")
    task.status = "cancelled"
    task.locked_at = None
    task.outcome_json = {**(task.outcome_json or {}), "cancelled_reason": "console"}
    await db.commit()
    return {"status": "cancelled", "task_id": task_id}
