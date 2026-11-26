"""When the clinic marks a visit done, the patient gets a feedback call two days later."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from org_env import env  # noqa: F401
from test_tenant_isolation import ACME_BOSS, GLOBEX_BOSS, world  # noqa: F401
from app.followup_scheduler import build_call_brief
from app.models import DoctorMeetingRequest, FollowUpTask, MedicineFeedback, MedicineOrder, Patient, Tenant

pytestmark = pytest.mark.asyncio


async def _booked_visit(world, with_patient=True):
    tid = world.tenants["acme"]
    async with world.maker() as db:
        t = await db.get(Tenant, tid)
        t.timezone = "Asia/Dubai"
        t.settings_json = {**(t.settings_json or {}), "calling_window": {"start": "00:00", "end": "23:59"}}
        patient = Patient(tenant_id=tid, first_name="Aadi", last_name="Haldar", phone_e164="+919000000001", identity_dob="1995-08-15")
        db.add(patient)
        await db.flush()
        m = DoctorMeetingRequest(tenant_id=tid, patient_id=patient.id if with_patient else None, doctor_name="Dr. Karim Haddad",
                                 reason="fever", status="booked", booked_slot="Monday 26 October at 10:00 AM", source="inbound",
                                 patient_name="Aadi Haldar")
        db.add(m)
        await db.commit()
        return m.id, patient.id


async def _tasks(world, meeting_id):
    async with world.maker() as db:
        return (await db.execute(select(FollowUpTask).where(FollowUpTask.meeting_id == meeting_id))).scalars().all()


async def test_marking_a_visit_done_schedules_one_feedback_call_two_days_later(world):
    mid, pid = await _booked_visit(world)
    res = await world.put(f"/api/sanjeevani/meetings/{mid}", user=ACME_BOSS, json={"status": "done"})
    assert res.status_code == 200 and res.json()["feedback_call_due_at"]
    tasks = await _tasks(world, mid)
    assert len(tasks) == 1
    t = tasks[0]
    assert (t.kind, t.status, t.patient_id, t.prescription_id) == ("feedback", "pending", pid, None)
    days = (t.due_at - datetime.utcnow()) / timedelta(days=1)
    assert 1.95 <= days <= 2.05, days
    async with world.maker() as db:
        assert (await db.get(DoctorMeetingRequest, mid)).completed_at is not None


async def test_saving_it_done_again_does_not_schedule_a_second_call(world):
    mid, _ = await _booked_visit(world)
    await world.put(f"/api/sanjeevani/meetings/{mid}", user=ACME_BOSS, json={"status": "done"})
    await world.put(f"/api/sanjeevani/meetings/{mid}", user=ACME_BOSS, json={"status": "done"})
    assert len(await _tasks(world, mid)) == 1


async def test_reopening_a_visit_cancels_the_call_that_was_waiting(world):
    mid, _ = await _booked_visit(world)
    await world.put(f"/api/sanjeevani/meetings/{mid}", user=ACME_BOSS, json={"status": "done"})
    await world.put(f"/api/sanjeevani/meetings/{mid}", user=ACME_BOSS, json={"status": "booked"})
    assert [t.status for t in await _tasks(world, mid)] == ["cancelled"]
    await world.put(f"/api/sanjeevani/meetings/{mid}", user=ACME_BOSS, json={"status": "done"})        # done again: a fresh call is scheduled
    assert sorted(t.status for t in await _tasks(world, mid)) == ["cancelled", "pending"]


async def test_a_visit_nobody_can_be_called_about_schedules_nothing(world):
    mid, _ = await _booked_visit(world, with_patient=False)
    res = await world.put(f"/api/sanjeevani/meetings/{mid}", user=ACME_BOSS, json={"status": "done"})
    assert res.status_code == 200 and res.json()["feedback_call_due_at"] is None
    assert await _tasks(world, mid) == []


async def test_another_company_cannot_mark_the_visit_done(world):
    mid, _ = await _booked_visit(world)
    assert (await world.put(f"/api/sanjeevani/meetings/{mid}", user=GLOBEX_BOSS, json={"status": "done"})).status_code == 404
    assert await _tasks(world, mid) == []


async def test_the_feedback_call_brief_names_the_visit_and_holds_no_medicine(world):
    mid, pid = await _booked_visit(world)
    async with world.maker() as db:
        patient, meeting = await db.get(Patient, pid), await db.get(DoctorMeetingRequest, mid)
    brief = build_call_brief(patient, None, "feedback", 99, meeting)
    assert "CALL TYPE: feedback" in brief and "Dr. Karim Haddad" in brief and f"MEETING ID: {mid}" in brief
    assert "record_visit_feedback" in brief and "confirm_identity" in brief
    assert "get_prescription_context" not in brief


async def test_feedback_about_a_visit_is_listed_with_the_patient_and_doctor(world):
    mid, pid = await _booked_visit(world)
    async with world.maker() as db:
        db.add(MedicineFeedback(tenant_id=world.tenants["acme"], meeting_id=mid, satisfaction=4, helped="yes", notes="kind doctor"))
        await db.commit()
    rows = (await world.get("/api/sanjeevani/feedback", user=ACME_BOSS)).json()["feedback"]
    assert rows[0]["patient"]["name"] == "Aadi Haldar" and rows[0]["doctor_name"] == "Dr. Karim Haddad"
    assert rows[0]["about_visit"] is True and rows[0]["comments"] == "kind doctor"
    assert (await world.get("/api/sanjeevani/feedback", user=GLOBEX_BOSS)).json()["feedback"] == []


async def test_only_appointments_the_agent_booked_are_listed_when_asked(world):
    mid, _ = await _booked_visit(world)
    async with world.maker() as db:
        db.add(DoctorMeetingRequest(tenant_id=world.tenants["acme"], doctor_name="Dr. X", status="requested", source="followup"))
        await db.commit()
    both = (await world.get("/api/sanjeevani/meetings", user=ACME_BOSS)).json()["meetings"]
    inbound = (await world.get("/api/sanjeevani/meetings?source=inbound", user=ACME_BOSS)).json()["meetings"]
    assert len(both) == 2 and [m["id"] for m in inbound] == [mid] and inbound[0]["source"] == "inbound"


async def test_orders_are_listed_per_company_and_only_a_scheduled_one_can_be_marked_delivered(world):
    async with world.maker() as db:
        o = MedicineOrder(tenant_id=world.tenants["acme"], reference="ORD-1", status="awaiting_payment", items_json=[{"drug_name": "Crocin"}])
        db.add(o)
        await db.commit()
        oid = o.id
    assert [x["reference"] for x in (await world.get("/api/sanjeevani/orders", user=ACME_BOSS)).json()["orders"]] == ["ORD-1"]
    assert (await world.get("/api/sanjeevani/orders", user=GLOBEX_BOSS)).json()["orders"] == []
    assert (await world.put(f"/api/sanjeevani/orders/{oid}", user=ACME_BOSS, json={"status": "delivered"})).status_code == 422
    assert (await world.put(f"/api/sanjeevani/orders/{oid}", user=GLOBEX_BOSS, json={"status": "cancelled"})).status_code == 404
    async with world.maker() as db:
        (await db.get(MedicineOrder, oid)).status = "scheduled"
        await db.commit()
    assert (await world.put(f"/api/sanjeevani/orders/{oid}", user=ACME_BOSS, json={"status": "delivered"})).json()["status"] == "delivered"
