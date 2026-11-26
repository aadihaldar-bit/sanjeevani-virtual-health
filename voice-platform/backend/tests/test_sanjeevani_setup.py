"""scripts/setup_sanjeevani.py, run against the real routes: it must create the company, upload the knowledge bases, save an
agent the builder accepts, and add the demo prescription, and it must be safe to run twice."""
import asyncio
import importlib.util
import json
import os

import pytest
from sqlalchemy import select

from org_env import ADMIN, env  # noqa: F401
from test_tenant_isolation import world  # noqa: F401
from app.models import Call, FollowUpTask, Patient, Tenant, Workflow, WorkflowNode

pytestmark = pytest.mark.asyncio

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "setup_sanjeevani.py")


def _load():
    spec = importlib.util.spec_from_file_location("setup_sanjeevani", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _run(world, mod, monkeypatch, *argv):
    loop = asyncio.get_running_loop()

    def call(method, url, headers, body=None, content_type=None):
        path = url.replace("http://test", "")
        hdrs = {"x-test-user": ADMIN, **{k: v for k, v in headers.items() if k.lower() == "x-tenant-id"}}
        kw = {}
        if body is not None:
            if isinstance(body, (bytes, bytearray)):
                kw["content"] = bytes(body)
                hdrs["content-type"] = content_type or "application/json"
            else:
                kw["json"] = body
        fut = asyncio.run_coroutine_threadsafe(world.client.request(method, path, headers=hdrs, **kw), loop)
        res = fut.result(timeout=120)
        try:
            return res.status_code, res.json()
        except ValueError:
            return res.status_code, {}

    monkeypatch.setattr(mod, "call", call)
    monkeypatch.setattr("sys.argv", ["setup_sanjeevani.py", "--api", "http://test", "--token", "x", *argv])
    await asyncio.to_thread(mod.main)


async def test_the_setup_builds_the_workspace_and_is_safe_to_repeat(world, monkeypatch, capsys):
    mod = _load()
    await _run(world, mod, monkeypatch, "--handover", "+971500000099", "--patient-phone", "+971500000001", "--demo-directory", "--calling-hours", "00:00-23:59")
    out = capsys.readouterr().out
    assert "created   Sanjeevani Holdings" in out and "uploaded  SANJEEVANI-KB-01_Medicine_Guide.md" in out and "Done." in out

    async with world.maker() as db:
        sanjeevani = (await db.execute(select(Tenant).where(Tenant.name == "Sanjeevani Holdings"))).scalars().one()
        assert (sanjeevani.settings_json or {}).get("calling_window") == {"start": "00:00", "end": "23:59"}
        names = {p.external_id: p.phone_e164 for p in (await db.execute(select(Patient).where(Patient.tenant_id == sanjeevani.id))).scalars().all()}
        assert names["aadi.haldar"] == "+919000000001" and names["aarit.haldar"] == "+919000000002"
        assert sanjeevani.timezone == "Asia/Dubai" and (sanjeevani.settings_json or {}).get("handover_number") == "+971500000099"
        wf = (await db.execute(select(Workflow).where(Workflow.tenant_id == sanjeevani.id))).scalars().one()
        assert wf.is_active is True
        nodes = (await db.execute(select(WorkflowNode).where(WorkflowNode.workflow_id == wf.id))).scalars().all()
        by_role = {n.data_json.get("role"): n.data_json for n in nodes if n.type == "agentNode"}
        specialists = {n.data_json["name"]: n.data_json for n in nodes if n.data_json.get("role") == "specialist"}
        assert set(by_role) == {"orchestrator", "specialist", "outbound"} and set(specialists) == {"Appointment Desk", "Pharmacy Desk", "Feedback Desk"}
        care = by_role["outbound"]                         # the step that makes the calls we place keeps every document
        assert "confirm_identity" in care["tools"] and "record_visit_feedback" in care["tools"] and len(care["kb_docs"]) == 8
        assert "SANJEEVANI-KB-01_Medicine_Guide.md" in care["kb_docs"]
        # each desk reads only its own documents and has only its own tools
        assert "find_doctors" in specialists["Appointment Desk"]["tools"] and "start_medicine_order" not in specialists["Appointment Desk"]["tools"]
        assert "SANJEEVANI-KB-08_Symptom_and_Specialty_Guide.md" in specialists["Appointment Desk"]["kb_docs"]
        assert "SANJEEVANI-KB-06_Pharmacy_Ordering_and_Delivery.md" in specialists["Pharmacy Desk"]["kb_docs"]
        assert "SANJEEVANI-KB-06_Pharmacy_Ordering_and_Delivery.md" not in specialists["Appointment Desk"]["kb_docs"]
        assert "check_feedback_eligibility" in specialists["Feedback Desk"]["tools"]
        assert by_role["orchestrator"]["greeting"].startswith("Hello, this is the Sanjeevani Holdings")
        patient = next(p for p in (await db.execute(select(Patient).where(Patient.tenant_id == sanjeevani.id))).scalars().all() if p.guardian_name == "Rahul")
        assert patient.phone_e164 == "+971500000001"
        tasks = (await db.execute(select(FollowUpTask).where(FollowUpTask.tenant_id == sanjeevani.id))).scalars().all()
        assert sorted(t.kind for t in tasks) == ["explain", "feedback"]
        other = (await db.execute(select(Workflow).where(Workflow.tenant_id == world.tenants["acme"]))).scalars().all()
        assert all("Sanjeevani" not in w.name for w in other)                    # nothing leaked into another company

    docs = (await world.client.get("/api/knowledge/documents", headers={"x-test-user": ADMIN, "X-Tenant-Id": str(sanjeevani.id)})).json()["documents"]
    assert len({d["name"] for d in docs}) >= 8

    # a second run changes nothing it should not
    await _run(world, mod, monkeypatch, "--handover", "+971500000099", "--patient-phone", "+971500000001", "--demo-directory", "--calling-hours", "00:00-23:59")
    again = capsys.readouterr().out
    assert "found     Sanjeevani Holdings" in again and "present   SANJEEVANI-KB-01_Medicine_Guide.md" in again and "updated   agent" in again
    async with world.maker() as db:
        assert len((await db.execute(select(Workflow).where(Workflow.tenant_id == sanjeevani.id))).scalars().all()) == 1
        assert len((await db.execute(select(Patient).where(Patient.tenant_id == sanjeevani.id))).scalars().all()) == 3
        assert len((await db.execute(select(FollowUpTask).where(FollowUpTask.tenant_id == sanjeevani.id))).scalars().all()) == 2


async def test_the_other_companys_administrator_cannot_do_this(world):
    acme_boss = "boss@acme.test"
    assert (await world.post("/api/tenants", user=acme_boss, json={"name": "Sneaky"})).status_code in (401, 403)
    assert (await world.put(f"/api/tenants/{world.tenants['globex']}/timezone", user=acme_boss, json={"timezone": "Asia/Dubai"})).status_code in (403, 404)
