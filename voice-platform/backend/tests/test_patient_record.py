"""GET /api/v1/patients/record: one patient's record for the company's own systems, never another company's rows."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base
from app.models import (Call, DoctorMeetingRequest, FollowUpTask, MedicineFeedback, MedicineOrder, Patient, Prescription,
                        PrescriptionItem)
from app.patient_record_api import AppointmentCreateIn, create_appointment, get_patient_record, patient_record

pytestmark = pytest.mark.asyncio

A, B = 6, 7
PHONE = "+971500000000"          # the same number at both companies: neither may see the other's


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        now = datetime.utcnow()
        for t in (A, B):
            p = Patient(tenant_id=t, external_id=f"MRN-{t}", first_name=f"Pat{t}", last_name="Test", phone_e164=PHONE)
            s.add(p)
            await s.flush()
            rx = Prescription(tenant_id=t, patient_id=p.id, external_id=f"RX-{t}", doctor_name=f"Dr {t}", issued_at=now)
            s.add(rx)
            await s.flush()
            s.add(PrescriptionItem(tenant_id=t, prescription_id=rx.id, drug_name=f"drug{t}", dose_text="1 tablet"))
            s.add(FollowUpTask(tenant_id=t, patient_id=p.id, prescription_id=rx.id, kind="explain", due_at=now, status="done"))
            s.add(MedicineFeedback(tenant_id=t, prescription_id=rx.id, helped="yes", satisfaction=5))
            s.add(DoctorMeetingRequest(tenant_id=t, patient_id=p.id, prescription_id=rx.id, doctor_name=f"Dr {t}",
                                       status="booked", slot_start=now + timedelta(days=2)))
            s.add(MedicineOrder(tenant_id=t, patient_id=p.id, reference=f"ORD-{t}", status="paid"))
            s.add(Call(tenant_id=t, called_number=PHONE, caller_number="+918000000000", summary=f"call {t}", started_at=now))
        # An appointment booked on an inbound call to company A knows only the caller's number.
        s.add(DoctorMeetingRequest(tenant_id=A, phone=PHONE, doctor_name="Dr Inbound", source="inbound", status="booked"))
        await s.commit()
        yield s
    await engine.dispose()


async def test_record_by_id_has_everything_and_only_this_company(db):
    r = await patient_record(db, A, external_id="MRN-6")
    assert r["patient"]["found"] and r["patient"]["name"] == "Pat6 Test"
    assert [p["external_id"] for p in r["prescriptions"]] == ["RX-6"]
    assert r["prescriptions"][0]["items"][0]["drug_name"] == "drug6"
    assert [t["status"] for t in r["follow_up_calls"]] == ["done"]
    assert {m["doctor_name"] for m in r["appointments"]} == {"Dr 6", "Dr Inbound"}
    assert [o["reference"] for o in r["orders"]] == ["ORD-6"]
    assert [c["summary"] for c in r["calls"]] == ["call 6"]
    assert r["feedback"][0]["satisfaction"] == 5
    text = repr(r)
    assert "RX-7" not in text and "ORD-7" not in text and "drug7" not in text and "call 7" not in text


async def test_record_by_phone_and_not_found(db):
    r = await patient_record(db, B, phone=PHONE)
    assert r["patient"]["external_id"] == "MRN-7" and [o["reference"] for o in r["orders"]] == ["ORD-7"]
    assert await patient_record(db, A, external_id="nobody") is None
    assert await patient_record(db, A, phone="+971599999999") is None


async def test_endpoint_refuses_a_key_without_a_company_and_empty_queries(db):
    with pytest.raises(HTTPException) as e:
        await get_patient_record(external_id="MRN-6", client=SimpleNamespace(tenant_id=None), db=db)
    assert e.value.status_code == 403
    with pytest.raises(HTTPException) as e:
        await get_patient_record(external_id=" ", phone=None, client=SimpleNamespace(tenant_id=A), db=db)
    assert e.value.status_code == 422
    with pytest.raises(HTTPException) as e:
        await get_patient_record(external_id="MRN-7", client=SimpleNamespace(tenant_id=A), db=db)
    assert e.value.status_code == 404          # company B's patient is simply not there for company A
    ok = await get_patient_record(external_id="MRN-6", client=SimpleNamespace(tenant_id=A), db=db)
    assert ok["patient"]["external_id"] == "MRN-6"


async def test_create_appointment_and_verify_record(db):
    app_in = AppointmentCreateIn(
        external_id="MRN-6",
        phone=PHONE,
        doctor_name="Dr Added",
        specialty="Cardiology",
        booked_slot="Friday 10:00 AM",
        reason="Heart checkup"
    )
    res = await create_appointment(body=app_in, client=SimpleNamespace(tenant_id=A), db=db)
    assert res["status"] == "booked"
    assert res["doctor_name"] == "Dr Added"
    assert res["external_ref"].startswith("BJ-")

    # Verify it shows up in company A's patient record
    rec = await patient_record(db, A, external_id="MRN-6")
    doc_names = [a["doctor_name"] for a in rec["appointments"]]
    assert "Dr Added" in doc_names

