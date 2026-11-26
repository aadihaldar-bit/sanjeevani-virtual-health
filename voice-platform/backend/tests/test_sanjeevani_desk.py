"""The Sanjeevani Appointment Desk and Feedback Desk tools, end to end against a real (temporary) database."""
import re
from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import database, tool_executor as te_mod
from app.database import Base
from app.models import Call, CallStatus, DoctorMeetingRequest, MedicineFeedback, Patient, Ticket

pytestmark = pytest.mark.asyncio

TENANT = 7
KNOWN_PHONE = "+971501112222"
NEW_PHONE = "+971509998888"


@pytest_asyncio.fixture
async def world(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'desk.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session", maker)

    async with maker() as db:
        known = Patient(tenant_id=TENANT, first_name="Aadi", last_name="Haldar", phone_e164=KNOWN_PHONE, identity_dob="1995-08-15")
        db.add(known)
        known_call = Call(tenant_id=TENANT, status=CallStatus.ACTIVE, source="smartflo-stream", caller_number=KNOWN_PHONE,
                          metadata_json={})
        new_call = Call(tenant_id=TENANT, status=CallStatus.ACTIVE, source="smartflo-stream", caller_number=NEW_PHONE,
                        metadata_json={})
        db.add_all([known_call, new_call])
        await db.commit()
        ids = {"patient": known.id, "known_call": known_call.id, "new_call": new_call.id}
    yield te_mod.ToolExecutor(), maker, ids
    await engine.dispose()


async def run(te, name, **args):
    if name == "find_doctors":
        args.setdefault("confirmed_with_caller", "yes")             # the agent has read the problem back and the caller agreed
    res = await te.execute(name, args)
    return res.get("result") if isinstance(res, dict) else res


def first_slot(text, doctor=None):
    """(doctor name, slot value) of the first opening listed."""
    for line in text.splitlines():
        m = re.search(r"\[slot=([0-9T:\-]+)\]", line)
        if m and (doctor is None or line.startswith(doctor)):
            return line.split(",")[0], m.group(1)
    raise AssertionError(f"no slot in: {text}")


async def test_find_doctors_names_the_specialty_the_fees_and_two_times(world):
    te, _, ids = world
    out = await run(te, "find_doctors", problem="chest pain and breathlessness", call_id=ids["new_call"])
    assert "Cardiology" in out
    assert "Dr. Faisal Al Mansoori" in out and "Dr. Mariam Khoury" in out        # senior and standard, both offered
    assert "700 dirhams" in out and "450 dirhams" in out
    assert len(re.findall(r"\[slot=", out)) == 4
    assert "senior consultant or the standard" in out


async def test_asking_for_a_senior_doctor_narrows_it(world):
    te, _, ids = world
    out = await run(te, "find_doctors", problem="skin rash", seniority="senior", call_id=ids["new_call"])
    assert "Dr. Rana Saleh" in out and "Dr. Tariq Aziz" not in out


async def test_the_cost_is_quoted_with_and_without_insurance(world):
    te, _, _ = world
    assert "pays 100 dirhams" in await run(te, "quote_visit_cost", doctor="Dr. Rana Saleh", insurer="Daman")
    assert "pays 500 dirhams" in await run(te, "quote_visit_cost", doctor="Dr. Rana Saleh", insurer="none")
    assert "not on our list" in await run(te, "quote_visit_cost", doctor="Dr. Rana Saleh", insurer="Imaginary Health")


async def test_a_new_caller_is_booked_and_becomes_a_patient(world):
    te, maker, ids = world
    listing = await run(te, "find_doctors", problem="skin rash", seniority="senior", call_id=ids["new_call"])
    doctor, slot = first_slot(listing)
    out = await run(te, "book_doctor_appointment", doctor=doctor, slot=slot, patient_name="Sara Ali", problem="itchy rash",
                    insurer="Daman", date_of_birth="3 March 1990", call_id=ids["new_call"], caller_number=NEW_PHONE)
    assert "Booked for Sara Ali with Dr. Rana Saleh" in out and "booking reference is B J - 0 0 1" in out
    assert "100 dirhams is payable" in out
    async with maker() as db:
        m = (await db.execute(select(DoctorMeetingRequest))).scalars().one()
        assert (m.status, m.source, m.doctor_name, m.insurer, m.fee_amount, m.copay_amount) == ("booked", "inbound", "Dr. Rana Saleh", "Daman", 500, 100)
        assert m.external_ref == "BJ-001" and m.slot_start is not None and m.tenant_id == TENANT
        p = await db.get(Patient, m.patient_id)
        assert (p.first_name, p.last_name, p.phone_e164, p.identity_dob) == ("Sara", "Ali", NEW_PHONE, "1990-03-03")


async def test_a_known_caller_is_booked_without_a_second_patient_record(world):
    te, maker, ids = world
    listing = await run(te, "find_doctors", problem="back pain", seniority="standard", call_id=ids["known_call"])
    doctor, slot = first_slot(listing)
    await run(te, "book_doctor_appointment", doctor=doctor, slot=slot, problem="back pain", call_id=ids["known_call"],
              caller_number=KNOWN_PHONE)
    async with maker() as db:
        assert len((await db.execute(select(Patient))).scalars().all()) == 1
        m = (await db.execute(select(DoctorMeetingRequest))).scalars().one()
        assert m.patient_id == ids["patient"]


async def test_a_time_that_was_never_offered_is_refused_and_a_taken_one_is_not_offered_again(world):
    te, maker, ids = world
    listing = await run(te, "find_doctors", problem="skin rash", seniority="senior", call_id=ids["new_call"])
    doctor, slot = first_slot(listing)
    bad = await run(te, "book_doctor_appointment", doctor=doctor, slot="2026-10-26T03:00", patient_name="Sara Ali",
                    call_id=ids["new_call"], caller_number=NEW_PHONE)
    assert "not available" in bad
    await run(te, "book_doctor_appointment", doctor=doctor, slot=slot, patient_name="Sara Ali", call_id=ids["new_call"], caller_number=NEW_PHONE)
    twice = await run(te, "book_doctor_appointment", doctor=doctor, slot=slot, patient_name="Omar Ali", call_id=ids["new_call"], caller_number="+971500000009")
    assert "not available" in twice
    after = await run(te, "find_doctors", problem="skin rash", seniority="senior", call_id=ids["new_call"])
    assert slot not in after


async def test_booking_needs_the_patients_name_when_the_caller_is_unknown(world):
    te, _, ids = world
    listing = await run(te, "find_doctors", problem="skin rash", seniority="senior", call_id=ids["new_call"])
    doctor, slot = first_slot(listing)
    assert "full name" in await run(te, "book_doctor_appointment", doctor=doctor, slot=slot, call_id=ids["new_call"], caller_number=NEW_PHONE)


async def _book_and_complete(te, maker, ids, call="known_call", phone=KNOWN_PHONE):
    listing = await run(te, "find_doctors", problem="knee pain", seniority="senior", call_id=ids[call])
    doctor, slot = first_slot(listing)
    await run(te, "book_doctor_appointment", doctor=doctor, slot=slot, problem="knee pain", call_id=ids[call], caller_number=phone)
    async with maker() as db:
        return (await db.execute(select(DoctorMeetingRequest))).scalars().one().id


async def test_feedback_is_not_open_to_someone_who_never_visited(world):
    te, _, ids = world
    await run(te, "confirm_identity", answer="15 August 1995", call_id=ids["known_call"], caller_number=KNOWN_PHONE)
    out = await run(te, "check_feedback_eligibility", call_id=ids["known_call"], caller_number=KNOWN_PHONE)
    assert out.startswith("NOT ELIGIBLE") and "no completed visit" in out
    unknown = await run(te, "check_feedback_eligibility", call_id=ids["new_call"], caller_number=NEW_PHONE)
    assert unknown.startswith("NOT ELIGIBLE") and "no patient record" in unknown


async def test_feedback_waits_until_the_clinic_marks_the_visit_done(world):
    te, maker, ids = world
    mid = await _book_and_complete(te, maker, ids)
    await run(te, "confirm_identity", answer="15 August 1995", call_id=ids["known_call"], caller_number=KNOWN_PHONE)
    waiting = await run(te, "check_feedback_eligibility", call_id=ids["known_call"], caller_number=KNOWN_PHONE)
    assert waiting.startswith("NOT ELIGIBLE YET")
    refused = await run(te, "record_visit_feedback", meeting_id=str(mid), rating="5", call_id=ids["known_call"], caller_number=KNOWN_PHONE)
    assert "marked completed" in refused
    async with maker() as db:
        assert (await db.execute(select(MedicineFeedback))).scalars().all() == []


async def test_feedback_asks_for_the_date_of_birth_before_it_says_anything_about_a_visit(world):
    te, maker, ids = world
    mid = await _book_and_complete(te, maker, ids)
    async with maker() as db:
        (await db.get(DoctorMeetingRequest, mid)).status = "done"
        await db.commit()
    blocked = await run(te, "check_feedback_eligibility", call_id=ids["known_call"], caller_number=KNOWN_PHONE)
    assert "date of birth" in blocked.lower() or "confirm" in blocked.lower()
    assert "Dr." not in blocked


async def test_a_completed_visit_makes_the_caller_eligible_and_feedback_is_saved_once(world):
    te, maker, ids = world
    mid = await _book_and_complete(te, maker, ids)
    async with maker() as db:
        (await db.get(DoctorMeetingRequest, mid)).status = "done"
        await db.commit()
    await run(te, "confirm_identity", answer="15 August 1995", call_id=ids["known_call"], caller_number=KNOWN_PHONE)
    ok = await run(te, "check_feedback_eligibility", call_id=ids["known_call"], caller_number=KNOWN_PHONE)
    assert ok.startswith("ELIGIBLE") and f"meeting_id={mid}" in ok
    saved = await run(te, "record_visit_feedback", meeting_id=str(mid), rating="5", medicine_helped="yes", comments="very good",
                      call_id=ids["known_call"], caller_number=KNOWN_PHONE)
    assert "saved" in saved
    async with maker() as db:
        fb = (await db.execute(select(MedicineFeedback))).scalars().one()
        assert (fb.meeting_id, fb.satisfaction, fb.helped, fb.notes) == (mid, 5, "yes", "very good")
        assert (await db.execute(select(Ticket))).scalars().all() == []                    # a happy patient raises nothing
    again = await run(te, "check_feedback_eligibility", call_id=ids["known_call"], caller_number=KNOWN_PHONE)
    assert again.startswith("NOT ELIGIBLE") and "already given" in again


async def test_unhappy_visit_feedback_raises_a_ticket_and_a_follow_up_meeting_request(world):
    te, maker, ids = world
    mid = await _book_and_complete(te, maker, ids)
    async with maker() as db:
        (await db.get(DoctorMeetingRequest, mid)).status = "done"
        await db.commit()
    await run(te, "confirm_identity", answer="15 August 1995", call_id=ids["known_call"], caller_number=KNOWN_PHONE)
    out = await run(te, "record_visit_feedback", meeting_id=str(mid), rating="2", medicine_helped="no", comments="pain is the same",
                    wants_doctor_followup="yes", call_id=ids["known_call"], caller_number=KNOWN_PHONE)
    assert "care team" in out
    async with maker() as db:
        assert len((await db.execute(select(Ticket))).scalars().all()) == 1
        meetings = (await db.execute(select(DoctorMeetingRequest).order_by(DoctorMeetingRequest.id))).scalars().all()
        assert [m.status for m in meetings] == ["done", "requested"]


async def test_search_knowledge_only_looks_in_the_desks_own_documents(world, monkeypatch):
    te, _, ids = world
    seen = {}

    class FakeRag:
        async def search(self, question, node_docs=None, tenant_id=None, top_k=3, **kw):
            seen.update(node_docs=node_docs, tenant_id=tenant_id)
            return "Cardiology: chest pain with sweating is an emergency."

    from app import knowledge_routes
    monkeypatch.setattr(knowledge_routes, "rag_engine", FakeRag())
    out = await run(te, "search_knowledge", question="chest pain", node_docs=["A.md"], call_id=ids["new_call"])
    assert "emergency" in out and seen == {"node_docs": ["A.md"], "tenant_id": TENANT}
    nothing = await run(te, "search_knowledge", question="chest pain", call_id=ids["new_call"])           # a desk with no documents
    assert "don't have that" in nothing


async def test_the_problem_is_read_back_before_any_doctor_is_searched(world):
    te, _, ids = world
    held = await run(te, "find_doctors", problem="pain in my eye", confirmed_with_caller="", call_id=ids["new_call"])
    assert held.startswith("Do not search yet") and "pain in my eye" in held and "if that is right" in held.lower()
    assert "[slot=" not in held and "Dr." not in held                       # no doctor is named before the caller agrees
    assert "[slot=" in await run(te, "find_doctors", problem="pain in my eye", confirmed_with_caller="yes", call_id=ids["new_call"])


async def test_an_eye_problem_is_sent_to_an_eye_doctor(world):
    te, _, ids = world
    out = await run(te, "find_doctors", problem="I have eye pain", call_id=ids["new_call"])
    assert "Ophthalmology" in out and "Dr. Hessa Al Suwaidi" in out and "Dr. Bilal Karimi" in out
    assert "Orthopedics" not in out
