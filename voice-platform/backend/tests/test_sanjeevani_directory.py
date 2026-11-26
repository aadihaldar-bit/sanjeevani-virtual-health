"""The patient directory, the 180-second delay after a prescription is saved, and the company's own calling hours."""
import json
from datetime import datetime

import pytest
from sqlalchemy import select

from org_env import env  # noqa: F401
from test_tenant_isolation import ACME_BOSS, GLOBEX_BOSS, world  # noqa: F401
from app import tenancy
from app.followup_scheduler import create_followup_tasks
from app.models import FollowUpTask, Patient, Prescription, Tenant

pytestmark = pytest.mark.asyncio

RX = {"prescription": {"external_id": "RX-D1", "doctor_name": "Dr. Karim Haddad"},
      "items": [{"drug_name": "Paracetamol", "strength": "500 mg", "form": "tablet", "dose_text": "1 tablet",
                 "timing": "every 6 hours", "duration_days": 3, "status": "confirmed"}]}


async def _company_with_directory(world, **window):
    tid = world.tenants["acme"]
    async with world.maker() as db:
        t = await db.get(Tenant, tid)
        t.timezone = "Asia/Dubai"
        if window:
            t.settings_json = {**(t.settings_json or {}), "calling_window": window}
        db.add_all([
            Patient(tenant_id=tid, external_id="aadi.haldar", first_name="Aadi", last_name="Haldar", phone_e164="+919000000001",
                    identity_dob="1995-08-15", preferred_language="en"),
            Patient(tenant_id=tid, external_id="aarit.haldar", first_name="Aarit", last_name="Haldar", phone_e164="+919000000002",
                    identity_dob="1996-01-26", preferred_language="en")])
        await db.commit()
    return tid


async def _upload(world, user, records):
    files = {"file": ("rx.json", json.dumps(records).encode(), "application/json")}
    return await world.client.post("/api/sanjeevani/prescriptions/upload", headers={"x-test-user": user}, files=files)


async def test_a_prescription_for_a_name_in_the_directory_calls_the_stored_number_after_180_seconds(world):
    await _company_with_directory(world, start="00:00", end="23:59")
    res = await _upload(world, ACME_BOSS, [{**RX, "patient": {"name": "Aadi Haldar"}}])
    assert res.status_code == 200 and res.json()["results"][0]["status"] == "created"
    async with world.maker() as db:
        task = (await db.execute(select(FollowUpTask).where(FollowUpTask.kind == "explain").order_by(FollowUpTask.id.desc()))).scalars().first()
        patient = await db.get(Patient, task.patient_id)
        assert patient.phone_e164 == "+919000000001" and patient.identity_dob == "1995-08-15"      # taken from the directory
        assert patient.tenant_id == world.tenants["acme"]
        wait = (task.due_at - datetime.utcnow()).total_seconds()
        assert 170 <= wait <= 185, wait                                                          # 180 s after the prescription was saved
        assert (task.window_start, task.window_end) == ("00:00", "23:59")
        feedback = (await db.execute(select(FollowUpTask).where(FollowUpTask.kind == "feedback", FollowUpTask.patient_id == patient.id))).scalars().all()
        assert len(feedback) == 1                                                                # the course length is known


async def test_a_username_resolves_to_the_right_person(world):
    await _company_with_directory(world, start="00:00", end="23:59")
    await _upload(world, ACME_BOSS, [{**RX, "prescription": {**RX["prescription"], "external_id": "RX-D2"}, "patient": {"username": "aarit.haldar"}}])
    async with world.maker() as db:
        task = (await db.execute(select(FollowUpTask).order_by(FollowUpTask.id.desc()))).scalars().first()
        assert (await db.get(Patient, task.patient_id)).phone_e164 == "+919000000002"


async def test_an_unknown_patient_without_a_number_is_refused_and_nothing_is_scheduled(world):
    await _company_with_directory(world)
    before = len((await world.get("/api/sanjeevani/followups", user=ACME_BOSS)).json()["followups"])
    res = await _upload(world, ACME_BOSS, [{**RX, "prescription": {"external_id": "RX-D3"}, "patient": {"name": "Nobody Atall"}}])
    out = res.json()["results"][0]
    assert out["status"] == "patient_not_found" and "Nobody Atall" in out["detail"]
    assert len((await world.get("/api/sanjeevani/followups", user=ACME_BOSS)).json()["followups"]) == before


async def test_the_directory_belongs_to_one_company(world):
    await _company_with_directory(world)
    res = await _upload(world, GLOBEX_BOSS, [{**RX, "prescription": {"external_id": "RX-D4"}, "patient": {"name": "Aadi Haldar"}}])
    assert res.json()["results"][0]["status"] == "patient_not_found"                             # Acme's directory is not Globex's
    assert "Aadi Haldar" in [p["name"] for p in (await world.get("/api/sanjeevani/patients", user=ACME_BOSS)).json()["patients"]]
    assert (await world.get("/api/sanjeevani/patients", user=GLOBEX_BOSS)).json()["patients"] == []


async def test_an_administrator_adds_a_patient_and_numbers_stay_hidden(world):
    await _company_with_directory(world)
    body = {"username": "new.person", "first_name": "New", "last_name": "Person", "phone_e164": "+971 50 123 4567", "identity_dob": "2001-02-03"}
    assert (await world.post("/api/sanjeevani/patients", user=ACME_BOSS, json=body)).status_code == 201
    listing = (await world.get("/api/sanjeevani/patients", user=ACME_BOSS)).json()["patients"]
    row = next(p for p in listing if p["username"] == "new.person")
    assert row["phone"] == "+971 ••• 4567" and "1234567" not in str(listing)
    assert tenancy.role_denial("viewer", "POST", "/api/sanjeevani/patients") is not None
    assert tenancy.role_denial("tenant_admin", "POST", "/api/sanjeevani/patients") is None


async def test_calling_hours_are_the_companys_own_and_are_validated(world):
    acme = world.tenants["acme"]
    ok = await world.put(f"/api/tenants/{acme}/calling-hours", user=ACME_BOSS, json={"start": "08:30", "end": "21:00"})
    assert ok.status_code == 200 and ok.json()["calling_window"] == {"start": "08:30", "end": "21:00"}
    for bad in ({"start": "25:00", "end": "21:00"}, {"start": "10:00", "end": "09:00"}, {"start": "9", "end": "20:00"}):
        assert (await world.put(f"/api/tenants/{acme}/calling-hours", user=ACME_BOSS, json=bad)).status_code == 422
    other = await world.put(f"/api/tenants/{world.tenants['globex']}/calling-hours", user=ACME_BOSS, json={"start": "08:00", "end": "20:00"})
    assert other.status_code in (403, 404)


async def test_outside_the_default_hours_the_first_call_waits_for_morning(world):
    tid = await _company_with_directory(world)                                                   # default 09:00 to 20:00 Gulf time
    async with world.maker() as db:
        patient = (await db.execute(select(Patient).where(Patient.tenant_id == tid))).scalars().first()
        presc = Prescription(tenant_id=tid, patient_id=patient.id, status="issued")
        db.add(presc)
        await db.flush()
        tasks = await create_followup_tasks(db, patient, presc, [], now=datetime(2026, 1, 15, 20, 0))      # 00:00 in Dubai
        explain = next(t for t in tasks if t.kind == "explain")
        assert explain.due_at == datetime(2026, 1, 16, 5, 0)                                     # 09:00 Dubai, not 180 s later


# --- the three demo prescriptions in docs/sanjeevani_demo/demo-prescriptions/ ----------------------------------------------

def _demo_files():
    import glob
    import os
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return sorted(glob.glob(os.path.join(here, "docs", "sanjeevani_demo", "demo-prescriptions", "*.json")))


async def test_every_demo_prescription_uploads_and_schedules_its_calls(world):
    files = _demo_files()
    assert len(files) == 3
    await _company_with_directory(world, start="00:00", end="23:59")
    for path in files:
        records = json.load(open(path, encoding="utf-8"))
        res = await _upload(world, ACME_BOSS, records)
        assert res.status_code == 200 and res.json()["results"][0]["status"] == "created", (path, res.text)
    async with world.maker() as db:
        tasks = (await db.execute(select(FollowUpTask))).scalars().all()
        assert sum(t.kind == "explain" for t in tasks) >= 3 and sum(t.kind == "feedback" for t in tasks) >= 3
        # the one with an unclear medicine keeps it out of what is read aloud
        from app.models import PrescriptionItem
        unclear = (await db.execute(select(PrescriptionItem).where(PrescriptionItem.status == "needs_clarification"))).scalars().all()
        assert [i.drug_name for i in unclear] == ["Azithromycin"] and unclear[0].dose_text is None
