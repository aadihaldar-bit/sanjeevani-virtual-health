"""The parts that make the patient-care calls run end to end: what the agent is told, how a finished call settles its
task, what the console pages and reports return, who may change what, and the company's time zone."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select

from org_env import env  # noqa: F401
from org_env import ADMIN as ADMIN_STL  # noqa: E402
from test_tenant_isolation import ACME_BOSS, GLOBEX_BOSS, world  # noqa: F401
from app import followup_scheduler as fs, media_bridge
from app.models import (
    Call, CallStatus, DoctorMeetingRequest, FollowUpTask, MedicineFeedback, Patient, PhoneNumber, Prescription, PrescriptionItem,
    Tenant, TranscriptMessage, Workflow,
)

pytestmark = pytest.mark.asyncio

SECRET_DRUG = "Zorbitrex"
SECRET_DOSE = "7.5 ml"


async def _care(world, tenant="acme", **patient_kw):
    """A patient with a prescription, a task and a call for that company."""
    tid = world.tenants[tenant]
    async with world.maker() as db:
        patient = Patient(tenant_id=tid, first_name="Aarav", guardian_name="Rahul", phone_e164="+971501234567",
                          identity_dob="2023-03-14", preferred_language="en", **patient_kw)
        db.add(patient)
        await db.flush()
        presc = Prescription(tenant_id=tid, patient_id=patient.id, doctor_name="Dr. Sonia", status="issued")
        db.add(presc)
        await db.flush()
        item = PrescriptionItem(tenant_id=tid, prescription_id=presc.id, drug_name=SECRET_DRUG, dose_text=SECRET_DOSE,
                                status="confirmed", duration_days=5)
        db.add(item)
        task = FollowUpTask(tenant_id=tid, patient_id=patient.id, prescription_id=presc.id, kind="explain",
                            due_at=datetime.utcnow(), status="calling", attempts=1)
        db.add(task)
        await db.flush()
        call = Call(tenant_id=tid, status=CallStatus.ACTIVE, source="sanjeevani-followup", started_at=datetime.utcnow(),
                    metadata_json={"task_id": task.id, "prescription_id": presc.id, "patient_id": patient.id, "kind": "explain"})
        db.add(call)
        await db.commit()
        return {"patient": patient.id, "presc": presc.id, "item": item.id, "task": task.id, "call": call.id}


# --- what the agent is told when the call connects --------------------------------------------------------------

async def test_the_call_brief_holds_no_medicine_or_condition(world):
    ids = await _care(world)
    async with world.maker() as db:
        patient = await db.get(Patient, ids["patient"])
        presc = await db.get(Prescription, ids["presc"])
    brief = fs.build_call_brief(patient, presc, "explain", ids["task"])
    assert SECRET_DRUG not in brief and SECRET_DOSE not in brief
    assert "confirm_identity" in brief and "get_prescription_context" in brief
    assert "Rahul" in brief and "Aarav" in brief and "Dr. Sonia" in brief


async def test_the_placed_call_carries_the_company_agent_and_the_safe_brief(world, monkeypatch):
    ids = await _care(world)
    stored = {}

    async def fake_place(number, caller_id=None, **kw):
        return {"call_sid": "SID-1", "clean_e164": number}

    async def fake_store(key, data):
        stored[key] = data

    import app.api_routes as api_routes
    import app.outbound_api as outbound_api
    monkeypatch.setattr(api_routes, "_place_outbound_call", fake_place)
    monkeypatch.setattr(outbound_api, "store_outbound_context", fake_store)
    monkeypatch.setattr(fs, "async_session", world.maker)
    async with world.maker() as db:
        for existing in (await db.execute(select(Workflow).where(Workflow.tenant_id == world.tenants["acme"]))).scalars().all():
            existing.is_active = False                    # the world gives every company a live agent already
        wf = Workflow(tenant_id=world.tenants["acme"], name="Patient care", is_active=True)
        db.add(wf)
        await db.commit()
        task = await db.get(FollowUpTask, ids["task"])
        call_id = await fs.place_followup_call(db, task)
        wf_id = wf.id
    ctx = stored["SID-1"]
    assert ctx["workflow_id"] == wf_id and ctx["tenant_id"] == world.tenants["acme"]
    assert SECRET_DRUG not in ctx["message_context"] and SECRET_DOSE not in ctx["message_context"]
    assert ctx["metadata"]["task_id"] == ids["task"] and call_id is not None
    assert "May I speak with Rahul" in ctx["greeting"]


async def test_each_company_calls_with_its_own_agent(world):
    async with world.maker() as db:
        for existing in (await db.execute(select(Workflow))).scalars().all():
            existing.is_active = False
        a = Workflow(tenant_id=world.tenants["acme"], name="Acme care", is_active=True)
        g = Workflow(tenant_id=world.tenants["globex"], name="Globex care", is_active=True)
        db.add_all([a, g])
        await db.commit()
        assert await fs.workflow_for_tenant(db, world.tenants["acme"]) == a.id
        assert await fs.workflow_for_tenant(db, world.tenants["globex"]) == g.id


# --- the bridge adopts an outbound call's context ----------------------------------------------------------------

async def test_the_bridge_takes_company_agent_and_task_from_the_outbound_context(world, monkeypatch):
    ids = await _care(world)
    monkeypatch.setattr(media_bridge, "async_session", world.maker)
    session = SimpleNamespace(_tenant_id=None, _workflow_id=[None], _db_call_id=[ids["call"]], _chat_history=[])
    session._take_over_planned_call = lambda c: media_bridge.PipecatMediaSession._take_over_planned_call(session, c)
    ctx = {"tenant_id": world.tenants["acme"], "workflow_id": 42, "message_context": "CALL TYPE: explain",
           "metadata": {"task_id": ids["task"], "patient_id": ids["patient"]}}
    await media_bridge.PipecatMediaSession._adopt_outbound_context(session, ctx)
    assert session._tenant_id == world.tenants["acme"] and session._workflow_id[0] == 42
    assert "CALL TYPE: explain" in session._chat_history[0]["content"]


def test_a_step_with_patient_care_tools_keeps_exactly_those_tools():
    # the agent's name says "patient", which used to pull in the clinic receptionist's tools
    node = {"name": "Patient care", "system_prompt": "You help patients with their medicines and the doctor.",
            "tools": ["confirm_identity", "get_prescription_context"]}
    assert media_bridge.PipecatMediaSession._resolve_gemini_tools(node) == ["confirm_identity", "get_prescription_context"]
    clinic = {"name": "Clinic receptionist", "system_prompt": "You book doctor appointments for the clinic."}
    assert "schedule_appointment" in media_bridge.PipecatMediaSession._resolve_gemini_tools(clinic)


# --- a finished call settles its task ----------------------------------------------------------------------------

async def _settle(world, monkeypatch, meta_extra=None, spoke=False):
    ids = await _care(world)
    monkeypatch.setattr(fs, "async_session", world.maker)
    async with world.maker() as db:
        call = await db.get(Call, ids["call"])
        call.metadata_json = {**call.metadata_json, **(meta_extra or {})}
        if spoke:
            db.add(TranscriptMessage(call_id=call.id, role="user", message="hello", sequence_number=1))
        await db.commit()
    status = await fs.settle_followup_call(ids["call"], now=datetime(2026, 1, 15, 12, 0))
    async with world.maker() as db:
        task = await db.get(FollowUpTask, ids["task"])
        presc = await db.get(Prescription, ids["presc"])
        return status, task, presc


async def test_a_confirmed_call_completes_the_task_and_marks_the_prescription(world, monkeypatch):
    status, task, presc = await _settle(world, monkeypatch, {"identity_verified": True}, spoke=True)
    assert status == "done" and task.status == "done" and presc.status == "explained"
    assert task.last_call_id is not None


async def test_nobody_answering_retries_later(world, monkeypatch):
    status, task, _ = await _settle(world, monkeypatch)
    assert status == "no_answer" and task.next_attempt_at == datetime(2026, 1, 15, 14, 0)


async def test_a_failed_identity_check_retries_later_and_counts(world, monkeypatch):
    status, task, presc = await _settle(world, monkeypatch, {"identity_failed": True}, spoke=True)
    assert status == "no_answer" and task.next_attempt_at is not None and presc.status == "issued"


async def test_a_patient_who_hangs_up_midway_is_called_again(world, monkeypatch):
    status, task, _ = await _settle(world, monkeypatch, spoke=True)
    assert status == "no_answer" and task.status == "no_answer"


async def test_a_requested_callback_or_a_cancelled_task_is_left_alone(world, monkeypatch):
    ids = await _care(world)
    monkeypatch.setattr(fs, "async_session", world.maker)
    async with world.maker() as db:
        task = await db.get(FollowUpTask, ids["task"])
        task.status, task.outcome_json = "pending", {"callback_requested": "tomorrow"}
        await db.commit()
    assert await fs.update_task_from_call(ids["task"], "done") == "pending"
    async with world.maker() as db:
        task = await db.get(FollowUpTask, ids["task"])
        task.status, task.outcome_json = "cancelled", {}
        await db.commit()
    assert await fs.update_task_from_call(ids["task"], "done") == "cancelled"


async def test_a_call_with_no_task_settles_nothing(world, monkeypatch):
    monkeypatch.setattr(fs, "async_session", world.maker)
    async with world.maker() as db:
        call = Call(tenant_id=world.tenants["acme"], status=CallStatus.ACTIVE, started_at=datetime.utcnow())
        db.add(call)
        await db.commit()
    assert await fs.settle_followup_call(call.id) is None


# --- the console pages and reports -------------------------------------------------------------------------------

async def test_listings_name_the_patient_and_hide_the_full_number(world):
    await _care(world)
    follow = (await world.get("/api/sanjeevani/followups", user=ACME_BOSS)).json()["followups"]
    assert follow and follow[0]["patient"]["name"] == "Aarav" and follow[0]["patient"]["contact"] == "Rahul"
    assert follow[0]["patient"]["phone"] == "+971 ••• 4567" and "1234567" not in str(follow)
    presc = (await world.get("/api/sanjeevani/prescriptions", user=ACME_BOSS)).json()["prescriptions"]
    assert presc[0]["patient"]["name"] == "Aarav" and presc[0]["items"][0]["drug_name"] == SECRET_DRUG


async def test_another_company_sees_none_of_it(world):
    await _care(world)
    for path, key in (("/api/sanjeevani/followups", "followups"), ("/api/sanjeevani/prescriptions", "prescriptions"),
                      ("/api/sanjeevani/feedback", "feedback"), ("/api/sanjeevani/meetings", "meetings")):
        assert (await world.get(path, user=GLOBEX_BOSS)).json()[key] == [], path


async def test_feedback_and_meetings_are_listed_and_the_clinic_can_confirm_one(world):
    ids = await _care(world)
    async with world.maker() as db:
        db.add(MedicineFeedback(tenant_id=world.tenants["acme"], prescription_item_id=ids["item"], prescription_id=ids["presc"],
                                helped="no", satisfaction=2, side_effects_text="a rash", call_id=ids["call"]))
        meeting = DoctorMeetingRequest(tenant_id=world.tenants["acme"], patient_id=ids["patient"], prescription_id=ids["presc"],
                                       doctor_name="Dr. Sonia", reason="not better", status="requested")
        db.add(meeting)
        await db.commit()
        meeting_id = meeting.id
    fb = (await world.get("/api/sanjeevani/feedback", user=ACME_BOSS)).json()["feedback"][0]
    assert fb["medicine"] == SECRET_DRUG and fb["patient"]["name"] == "Aarav" and fb["side_effects_text"] == "a rash"
    meetings = (await world.get("/api/sanjeevani/meetings", user=ACME_BOSS)).json()["meetings"]
    assert meetings[0]["status"] == "requested" and meetings[0]["patient"]["name"] == "Aarav"

    assert (await world.put(f"/api/sanjeevani/meetings/{meeting_id}", user=ACME_BOSS, json={"status": "booked"})).status_code == 422
    ok = await world.put(f"/api/sanjeevani/meetings/{meeting_id}", user=ACME_BOSS,
                         json={"status": "booked", "booked_slot": "Wednesday 11:00"})
    assert ok.status_code == 200 and ok.json()["status"] == "booked" and ok.json()["booked_slot"] == "Wednesday 11:00"
    assert (await world.put(f"/api/sanjeevani/meetings/{meeting_id}", user=GLOBEX_BOSS, json={"status": "done"})).status_code == 404
    assert (await world.put(f"/api/sanjeevani/meetings/{meeting_id}", user=ACME_BOSS, json={"status": "nonsense"})).status_code == 422


async def test_the_summary_counts_tasks_and_prescriptions(world):
    await _care(world)
    summary = (await world.get("/api/sanjeevani/reports/summary", user=ACME_BOSS)).json()
    assert summary["prescriptions"] == 1 and summary["tasks_by_status"].get("calling") == 1
    other = (await world.get("/api/sanjeevani/reports/summary", user=GLOBEX_BOSS)).json()
    assert other["prescriptions"] == 0 and other["tasks_by_status"] == {}


async def test_only_an_administrator_changes_a_meeting(world):
    from app import tenancy
    for role in ("viewer", "operator", "manager"):
        assert tenancy.role_denial(role, "PUT", "/api/sanjeevani/meetings/3") is not None
    assert tenancy.role_denial("tenant_admin", "PUT", "/api/sanjeevani/meetings/3") is None


# --- the company time zone ---------------------------------------------------------------------------------------

async def test_a_company_administrator_sets_the_time_zone_and_the_scheduler_uses_it(world, monkeypatch):
    acme = world.tenants["acme"]
    assert (await world.get("/api/timezones", user=ACME_BOSS)).json()["timezones"][0]["id"] == "Asia/Dubai"
    res = await world.put(f"/api/tenants/{acme}/timezone", user=ACME_BOSS, json={"timezone": "Asia/Dubai"})
    assert res.status_code == 200 and res.json()["timezone"] == "Asia/Dubai"
    assert (await world.put(f"/api/tenants/{acme}/timezone", user=ACME_BOSS, json={"timezone": "Mars/Base"})).status_code == 422
    assert (await world.put(f"/api/tenants/{world.tenants['globex']}/timezone", user=ACME_BOSS,
                            json={"timezone": "Asia/Dubai"})).status_code in (403, 404)
    async with world.maker() as db:
        tz = await fs.tenant_timezone(db, acme)
    # 05:30 UTC is 09:30 in Dubai (inside the calling hours) and 11:00 in Kolkata
    assert fs.within_window(datetime(2026, 1, 15, 5, 30), tz) is True
    assert fs.within_window(datetime(2026, 1, 15, 17, 0), tz) is False        # 21:00 in Dubai


# --- the look-up tool answers from the call's own company documents, after identity --------------------------------

async def _lookup_world(world, monkeypatch, tmp_path):
    from app import database, tool_executor as te_mod
    monkeypatch.setattr(database, "async_session", world.maker)
    ids = await _care(world)
    for tenant, text in (("acme", "# Tablet storage\nAcme says keep the syrup in the fridge door and discard after ten days."),
                         ("globex", "# Tablet storage\nGlobex says keep the syrup in a cupboard and discard after thirty days.")):
        path = tmp_path / f"{tenant}.md"
        path.write_text(text, encoding="utf-8")
        await world.engine.ingest_document(str(path), f"doc-{tenant}", f"{tenant}.md", node_id=None, tenant_id=world.tenants[tenant])
    return te_mod.ToolExecutor(), ids


async def test_the_look_up_refuses_before_identity(world, monkeypatch, tmp_path):
    te, ids = await _lookup_world(world, monkeypatch, tmp_path)
    out = (await te.execute("look_up_care_information", {"question": "is the fridge door right for the syrup", "call_id": ids["call"]})).get("result")
    assert "confirm your identity" in out.lower() and "fridge" not in out.lower()


async def test_the_look_up_answers_only_from_the_calls_own_company(world, monkeypatch, tmp_path):
    te, ids = await _lookup_world(world, monkeypatch, tmp_path)
    await te.execute("confirm_identity", {"answer": "14/03/2023", "call_id": ids["call"]})
    out = (await te.execute("look_up_care_information", {"question": "is the fridge door right for the syrup", "call_id": ids["call"]})).get("result")
    assert "fridge" in out.lower() and "cupboard" not in out.lower() and "globex" not in out.lower()


async def test_the_look_up_does_nothing_for_a_call_with_no_company(world, monkeypatch, tmp_path):
    te, ids = await _lookup_world(world, monkeypatch, tmp_path)
    await te.execute("confirm_identity", {"answer": "14/03/2023", "call_id": ids["call"]})
    async with world.maker() as db:
        (await db.get(Call, ids["call"])).tenant_id = None
        await db.commit()
    out = (await te.execute("look_up_care_information", {"question": "is the fridge door right for the syrup", "call_id": ids["call"]})).get("result")
    assert "fridge" not in out.lower() and "cupboard" not in out.lower() and "pharmacist" in out.lower()


# --- live agents are per company: say whose, allow taking one offline, and never borrow another company's ------------

async def test_the_agent_list_says_whose_agent_is_live_and_one_can_be_taken_offline(world):
    cards = (await world.get("/api/agents/", user=ADMIN_STL)).json()
    live = {c["company"]: c["name"] for c in cards if c["is_active"]}
    assert set(live) == {"STL Digital", "Acme", "Globex"}                       # one live agent per company, each named
    acme_agent = next(c for c in cards if c["is_active"] and c["company"] == "Acme")
    assert (await world.post(f"/api/agents/{acme_agent['id']}/deactivate", user=GLOBEX_BOSS)).status_code == 404
    assert (await world.post(f"/api/agents/{acme_agent['id']}/deactivate", user=ACME_BOSS)).status_code == 200
    after = {c["id"]: c["is_active"] for c in (await world.get("/api/agents/", user=ADMIN_STL)).json()}
    assert after[acme_agent["id"]] is False and sum(after.values()) == 2          # only Acme's went offline


async def test_the_agent_list_names_the_numbers_each_agent_answers(world):
    """A number given an agent reaches it live or not, so the card must say which numbers an agent holds. A company sees its own."""
    acme, globex = world.tenants["acme"], world.tenants["globex"]
    async with world.maker() as db:
        acme_agent = (await db.execute(select(Workflow).where(Workflow.tenant_id == acme))).scalars().first()
        globex_agent = (await db.execute(select(Workflow).where(Workflow.tenant_id == globex))).scalars().first()
        db.add_all([PhoneNumber(number="+918069879488", tenant_id=acme, workflow_id=acme_agent.id),
                    PhoneNumber(number="+918069879499", tenant_id=globex, workflow_id=globex_agent.id, direction="inbound"),
                    PhoneNumber(number="+918069879411", tenant_id=acme, workflow_id=acme_agent.id, direction="outbound"),   # never answers
                    PhoneNumber(number="+918069879422", tenant_id=acme)])                                                   # no agent of its own
        await db.commit()
    mine = {c["name"]: c["numbers"] for c in (await world.get("/api/agents/", user=ACME_BOSS)).json()}
    assert mine == {"acme desk": ["+918069879488"]}                                  # its own number only: not Globex's, not outbound-only
    everyone = {c["name"]: c["numbers"] for c in (await world.get("/api/agents/", user=ADMIN_STL)).json()}
    assert everyone == {"stl desk": [], "acme desk": ["+918069879488"], "globex desk": ["+918069879499"]}


async def test_a_company_with_no_live_agent_is_not_called_with_anothers(world, monkeypatch):
    ids = await _care(world)
    monkeypatch.setattr(fs, "async_session", world.maker)
    async with world.maker() as db:
        for w in (await db.execute(select(Workflow).where(Workflow.tenant_id == world.tenants["acme"]))).scalars().all():
            w.is_active = False
        await db.commit()
        task = await db.get(FollowUpTask, ids["task"])
        assert await fs.place_followup_call(db, task) is None
    async with world.maker() as db:
        task = await db.get(FollowUpTask, ids["task"])
        assert task.status == "needs_human" and task.outcome_json["cancelled_reason"] == "no_live_agent"


async def test_the_stream_takes_over_the_scheduled_call_row_instead_of_leaving_a_second_one(world, monkeypatch):
    """The carrier's stream arrives with its own call id and the bridge makes a row for it (stamped with the platform's company).
    The scheduled row, which belongs to the right company and task, must be the one that carries on."""
    ids = await _care(world)
    monkeypatch.setattr(media_bridge, "async_session", world.maker)
    async with world.maker() as db:
        extra = Call(tenant_id=world.tenants["stl"], call_sid="CAGE3-T10-123.456", caller_number="+919000000002", called_number="+918065250182",
                     status=CallStatus.ACTIVE, live_status="in-progress", source="smartflo-stream", current_agent="IT Service Desk Reception",
                     started_at=datetime.utcnow())
        db.add(extra)
        await db.commit()
        extra_id = extra.id
    session = SimpleNamespace(_tenant_id=None, _workflow_id=[None], _db_call_id=[extra_id], _chat_history=[])
    session._take_over_planned_call = lambda c: media_bridge.PipecatMediaSession._take_over_planned_call(session, c)
    ctx = {"tenant_id": world.tenants["acme"], "workflow_id": 7, "call_row_id": ids["call"], "message_context": "CALL TYPE: explain",
           "metadata": {"task_id": ids["task"], "patient_id": ids["patient"]}}
    await media_bridge.PipecatMediaSession._adopt_outbound_context(session, ctx)
    assert session._db_call_id[0] == ids["call"]                                      # the call carries on as the scheduled row
    async with world.maker() as db:
        assert await db.get(Call, extra_id) is None                                   # no second row left behind
        call = await db.get(Call, ids["call"])
        assert call.call_sid == "CAGE3-T10-123.456" and call.caller_number == "+919000000002" and call.live_status == "in-progress"
        assert call.tenant_id == world.tenants["acme"]                                # the right company, not the platform's
        assert call.metadata_json["task_id"] == ids["task"]


async def test_the_bridge_corrects_the_company_of_a_call_row_it_made_itself(world, monkeypatch):
    monkeypatch.setattr(media_bridge, "async_session", world.maker)
    async with world.maker() as db:
        own = Call(tenant_id=world.tenants["stl"], call_sid="X-1", status=CallStatus.ACTIVE, started_at=datetime.utcnow())
        db.add(own)
        await db.commit()
        own_id = own.id
    session = SimpleNamespace(_tenant_id=None, _workflow_id=[None], _db_call_id=[own_id], _chat_history=[])
    session._take_over_planned_call = lambda c: media_bridge.PipecatMediaSession._take_over_planned_call(session, c)
    await media_bridge.PipecatMediaSession._adopt_outbound_context(session, {"tenant_id": world.tenants["acme"], "metadata": {"kind": "explain"}})
    async with world.maker() as db:
        assert (await db.get(Call, own_id)).tenant_id == world.tenants["acme"]


async def test_the_builder_says_which_agents_run_on_gemini_live(world, monkeypatch):
    monkeypatch.setenv("GEMINI_LIVE_ENABLED", "true")
    monkeypatch.setenv("GEMINI_LIVE_API_KEY", "test-key")
    monkeypatch.delenv("GEMINI_LIVE_WORKFLOW_IDS", raising=False)
    live = (await world.get("/api/workflows/options", user=ACME_BOSS)).json()["gemini_live"]
    assert live["for_all"] is True
    monkeypatch.setenv("GEMINI_LIVE_ENABLED", "false")
    off = (await world.get("/api/workflows/options", user=ACME_BOSS)).json()["gemini_live"]
    assert off["for_all"] is False and off["workflow_ids"] == []
