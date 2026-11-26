"""Sanjeevani care tools: identity gate before any medicine data, and unhappy feedback -> meeting + ticket."""
from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import database, tool_executor as te_mod
from app.database import Base
from app.models import (
    Call,
    CallStatus,
    DoctorMeetingRequest,
    FollowUpTask,
    MedicineFeedback,
    Patient,
    Prescription,
    PrescriptionItem,
    Ticket,
)

pytestmark = pytest.mark.asyncio

TENANT = 5


@pytest_asyncio.fixture
async def world(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'tools.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session", maker)        # tools open their own session

    async with maker() as db:
        patient = Patient(tenant_id=TENANT, first_name="Aarav", phone_e164="+971500000001",
                          identity_dob="2015-05-20", preferred_language="en")
        db.add(patient)
        await db.flush()
        presc = Prescription(tenant_id=TENANT, patient_id=patient.id, doctor_name="Dr Khan", status="issued")
        db.add(presc)
        await db.flush()
        confirmed = PrescriptionItem(tenant_id=TENANT, prescription_id=presc.id, drug_name="Amoxicillin",
                                     dose_text="5 ml twice a day", status="confirmed",
                                     plain_summary_en="Give one spoonful morning and night.")
        unclear = PrescriptionItem(tenant_id=TENANT, prescription_id=presc.id, drug_name="Ephodrex",
                                   dose_text="SECRET DOSE", status="needs_clarification",
                                   open_question="dose unclear")
        db.add_all([confirmed, unclear])
        task = FollowUpTask(tenant_id=TENANT, patient_id=patient.id, prescription_id=presc.id,
                            kind="explain", due_at=datetime.utcnow(), status="calling")
        db.add(task)
        await db.flush()
        call = Call(tenant_id=TENANT, status=CallStatus.ACTIVE, source="sanjeevani-followup",
                    metadata_json={"task_id": task.id, "prescription_id": presc.id,
                                   "patient_id": patient.id, "kind": "explain"})
        db.add(call)
        await db.commit()
        ids = {"patient": patient.id, "presc": presc.id, "item": confirmed.id,
               "task": task.id, "call": call.id}

    te = te_mod.ToolExecutor()
    yield te, maker, ids
    await engine.dispose()


async def _meta(maker, call_id):
    async with maker() as db:
        return dict((await db.get(Call, call_id)).metadata_json or {})


async def _verify(te, ids):
    out = await te.execute("confirm_identity", {"answer": "20/05/2015", "call_id": ids["call"]})
    assert "confirmed your identity" in (out.get("result") or "").lower()


async def _run(te, name, **args):
    res = await te.execute(name, args)
    return res.get("result") if isinstance(res, dict) else res


async def test_prescription_context_refuses_before_identity(world):
    te, maker, ids = world
    out = await _run(te, "get_prescription_context", task_id=str(ids["task"]), call_id=ids["call"])
    assert "confirm your identity" in out.lower()
    assert "Amoxicillin" not in out


async def test_identity_then_context_hides_needs_clarification_dose(world):
    te, maker, ids = world
    ok = await _run(te, "confirm_identity", answer="20/05/2015", call_id=ids["call"])
    assert "confirmed your identity" in ok.lower()
    assert (await _meta(maker, ids["call"])).get("identity_verified") is True      # on the call record, not in memory

    ctx = await _run(te, "get_prescription_context", task_id=str(ids["task"]), call_id=ids["call"])
    assert "Aarav" in ctx and "Amoxicillin" in ctx
    assert "SECRET DOSE" not in ctx                       # needs_clarification dose is never read
    assert "pharmacist will confirm" in ctx.lower()


async def test_wrong_dob_keeps_the_gate_closed(world):
    te, maker, ids = world
    bad = await _run(te, "confirm_identity", answer="01/01/1990", call_id=ids["call"])
    assert "doesn't match" in bad.lower()
    meta = await _meta(maker, ids["call"])
    assert not meta.get("identity_verified") and meta.get("identity_attempts") == 1 and not meta.get("identity_failed")


async def test_unhappy_feedback_creates_meeting_and_ticket(world):
    te, maker, ids = world
    await _verify(te, ids)
    msg = await _run(te, "record_feedback", prescription_id=str(ids["presc"]), item_id=str(ids["item"]),
                     taken="yes", helped="no", satisfaction="2", side_effects="a bad rash",
                     call_id=ids["call"], caller_number="+971500000001")
    assert "care team" in msg.lower() or "working day" in msg.lower()
    async with maker() as db:
        fb = (await db.execute(select(MedicineFeedback))).scalars().all()
        dmr = (await db.execute(select(DoctorMeetingRequest))).scalars().all()
        tk = (await db.execute(select(Ticket))).scalars().all()
    assert len(fb) == 1 and fb[0].tenant_id == TENANT and fb[0].helped == "no"
    assert len(dmr) == 1 and dmr[0].doctor_name == "Dr Khan"
    assert len(tk) == 1 and tk[0].priority == "high"      # side effect -> high (KB-04)


async def test_happy_feedback_creates_no_meeting(world):
    te, maker, ids = world
    await _verify(te, ids)
    await _run(te, "record_feedback", prescription_id=str(ids["presc"]), item_id=str(ids["item"]),
               taken="yes", helped="yes", satisfaction="5", side_effects="",
               call_id=ids["call"], caller_number="+971500000001")
    async with maker() as db:
        assert (await db.execute(select(MedicineFeedback))).scalars().all()
        assert (await db.execute(select(DoctorMeetingRequest))).scalars().all() == []


async def test_mark_do_not_call_stops_future_calls(world):
    te, maker, ids = world
    # a pending task that should be cancelled
    async with maker() as db:
        db.add(FollowUpTask(tenant_id=TENANT, patient_id=ids["patient"], prescription_id=ids["presc"],
                            kind="feedback", due_at=datetime.utcnow(), status="pending"))
        await db.commit()

    await _run(te, "mark_do_not_call", call_id=ids["call"], confirm="yes")
    async with maker() as db:
        patient = await db.get(Patient, ids["patient"])
        pending = (await db.execute(select(FollowUpTask).where(FollowUpTask.status == "pending"))).scalars().all()
    assert patient.do_not_call is True and pending == []


# --- the identity gate is stored on the call, covers every patient-data tool, and locks after two misses ----------

async def test_two_wrong_answers_lock_the_call_even_for_a_correct_third(world):
    te, maker, ids = world
    first = await _run(te, "confirm_identity", answer="01/01/1990", call_id=ids["call"])
    assert "doesn't match" in first.lower()
    second = await _run(te, "confirm_identity", answer="02/02/1991", call_id=ids["call"])
    assert "try again another time" in second.lower() and "goodbye" in second.lower()
    third = await _run(te, "confirm_identity", answer="20/05/2015", call_id=ids["call"])      # the right answer, too late
    assert "not able to confirm" in third.lower()
    meta = await _meta(maker, ids["call"])
    assert meta.get("identity_failed") is True and not meta.get("identity_verified")
    ctx = await _run(te, "get_prescription_context", task_id=str(ids["task"]), call_id=ids["call"])
    assert "Amoxicillin" not in ctx and "not able to confirm" in ctx.lower()


async def test_a_patient_with_no_date_of_birth_on_file_cannot_be_verified(world):
    te, maker, ids = world
    async with maker() as db:
        (await db.get(Patient, ids["patient"])).identity_dob = None
        await db.commit()
    out = await _run(te, "confirm_identity", answer="20/05/2015", call_id=ids["call"])
    assert "not able to confirm" in out.lower()
    assert (await _meta(maker, ids["call"])).get("identity_failed") is True


@pytest.mark.parametrize("tool,args", [
    ("record_understanding", {"item_id": "1", "understood": "yes"}),
    ("record_feedback", {"taken": "yes", "helped": "no", "satisfaction": "1", "side_effects": "rash"}),
    ("request_doctor_meeting", {"reason": "not better", "preferred_times": "Wednesday"}),
])
async def test_no_patient_data_tool_works_before_identity(world, tool, args):
    te, maker, ids = world
    out = await _run(te, tool, call_id=ids["call"], **args)
    assert "confirm your identity" in out.lower()
    async with maker() as db:
        assert (await db.execute(select(MedicineFeedback))).scalars().all() == []
        assert (await db.execute(select(DoctorMeetingRequest))).scalars().all() == []
        assert (await db.execute(select(Ticket))).scalars().all() == []


async def test_do_not_call_and_callback_work_without_identity(world):
    """A wrong person who answers can still ask not to be called, or for another time."""
    te, maker, ids = world
    assert "call you back" in (await _run(te, "schedule_callback", when="tomorrow", call_id=ids["call"])).lower()
    assert "won't call you again" in (await _run(te, "mark_do_not_call", call_id=ids["call"], confirm="yes")).lower()


async def test_the_identity_state_survives_a_new_tool_executor(world):
    """Nothing is kept in the process: a restarted backend, or another worker, sees the same answer."""
    te, maker, ids = world
    await _verify(te, ids)
    fresh = te_mod.ToolExecutor()
    ctx = await _run(fresh, "get_prescription_context", task_id=str(ids["task"]), call_id=ids["call"])
    assert "Amoxicillin" in ctx


async def test_an_answer_that_is_not_a_date_does_not_use_up_a_try(world):
    """The model can call the tool before the person has said anything: that must not count as a wrong answer."""
    te, maker, ids = world
    for nothing in ("", "Ja.", "in English", "What do you want to tell?"):
        out = await _run(te, "confirm_identity", answer=nothing, call_id=ids["call"])
        assert "didn't catch a date" in out.lower()
    meta = await _meta(maker, ids["call"])
    assert not meta.get("identity_attempts") and not meta.get("identity_failed")
    ok = await _run(te, "confirm_identity", answer="20/05/2015", call_id=ids["call"])
    assert "confirmed your identity" in ok.lower()
