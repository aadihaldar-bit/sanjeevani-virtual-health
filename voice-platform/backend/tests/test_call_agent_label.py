"""The console names the agent on a live call from the moment it connects. A call row used to be made with a fixed agent
name ("IT Service Desk Reception"), so a call to any other agent showed the wrong one until a handover, if there was one."""
import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

from org_env import env  # noqa: F401
from test_tenant_isolation import world  # noqa: F401
from app import media_bridge, outbound_api
from app.models import Call, CallStatus, Workflow, WorkflowEdge, WorkflowNode

pytestmark = pytest.mark.asyncio


class _NoGreeting:
    async def get_initial_greeting(self, *a, **k):
        raise RuntimeError("no stored greeting")


def _session(**kw):
    """The bridge object without its speech services: just what choosing an agent and recording it touches."""
    s = object.__new__(media_bridge.PipecatMediaSession)
    s._workflow_id, s._db_call_id, s._caller_phone, s._tenant_id = [None], [None], [None], None
    s._route_found, s._router_service = True, _NoGreeting()
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def _node(wf_id, node_id, name, prompt, y, **data):
    return WorkflowNode(id=node_id, workflow_id=wf_id, type="agentNode", position_x=0, position_y=y,
                        data_json={"name": name, "system_prompt": prompt, **data})


async def _workflow(world, tenant, name, nodes_for, edges_for=None):
    async with world.maker() as db:
        wf = Workflow(tenant_id=world.tenants[tenant], name=name, is_active=False)
        db.add(wf)
        await db.flush()
        for n in nodes_for(wf.id):
            db.add(n)
        await db.flush()
        for e in (edges_for(wf.id) if edges_for else []):
            db.add(e)
        await db.commit()
        return wf.id


async def _blank_call(world, tenant):
    async with world.maker() as db:
        call = Call(tenant_id=world.tenants[tenant], call_sid=f"SID-{datetime.utcnow().timestamp()}", status=CallStatus.ACTIVE,
                    live_status="in-progress", started_at=datetime.utcnow())
        db.add(call)
        await db.commit()
        return call.id


@pytest.fixture
def broadcasts(monkeypatch, world):
    sent = []

    async def fake(message):
        sent.append(message)

    monkeypatch.setattr(media_bridge, "async_session", world.maker)
    monkeypatch.setattr(media_bridge.dashboard_manager, "broadcast", fake)
    return sent


async def test_a_single_agent_workflow_is_named_on_the_call(world, broadcasts):
    wf = await _workflow(world, "acme", "Lenovo Laptop Assistant",
                         lambda w: [_node(w, f"agent-{w}", "Lenovo Laptop Assistant", "You help with Lenovo laptops.", 100)])
    call_id = await _blank_call(world, "acme")
    session = _session(_workflow_id=[wf], _db_call_id=[call_id], _tenant_id=world.tenants["acme"])

    await session._gemini_persona()
    await session._record_current_agent(session._gemini_agent_name)

    async with world.maker() as db:
        assert (await db.get(Call, call_id)).current_agent == "Lenovo Laptop Assistant"
    assert [m["call"]["current_agent"] for m in broadcasts if m["type"] == "call_updated"] == ["Lenovo Laptop Assistant"]


async def test_a_workflow_with_an_orchestrator_is_named_by_the_agent_that_answers_first(world, broadcasts):
    def nodes(w):
        return [_node(w, f"spec-{w}", "Appointment Desk", "You book appointments.", 100, role="specialist"),
                _node(w, f"front-{w}", "Front desk", "You greet and route.", 200, role="orchestrator"),
                _node(w, f"out-{w}", "Patient care assistant", "You call patients.", 300, role="outbound")]

    wf = await _workflow(world, "acme", "Care", nodes)
    inbound = _session(_workflow_id=[wf], _db_call_id=[await _blank_call(world, "acme")], _tenant_id=world.tenants["acme"])
    await inbound._gemini_persona()
    assert inbound._gemini_agent_name == "Front desk"                  # the orchestrator takes a call coming in

    outbound = _session(_workflow_id=[wf], _db_call_id=[await _blank_call(world, "acme")], _tenant_id=world.tenants["acme"],
                        _outbound_ctx={"message_context": "why we are calling", "greeting": "Hello"})
    await outbound._gemini_persona()
    assert outbound._gemini_agent_name == "Patient care assistant"      # the outbound step makes a call we place


async def test_the_label_is_the_workflow_name_when_no_step_has_a_name(world, broadcasts):
    wf = await _workflow(world, "acme", "Billing line", lambda w: [
        WorkflowNode(id=f"a-{w}", workflow_id=w, type="agentNode", position_x=0, position_y=0, data_json={"system_prompt": "You help."})])
    session = _session(_workflow_id=[wf], _db_call_id=[await _blank_call(world, "acme")], _tenant_id=world.tenants["acme"])
    await session._gemini_persona()
    assert session._gemini_agent_name == "Billing line"


async def test_recording_the_agent_never_takes_a_call_down(world, monkeypatch):
    def broken():
        raise RuntimeError("database is down")

    monkeypatch.setattr(media_bridge, "async_session", broken)
    await _session(_db_call_id=[1])._record_current_agent("Front desk")       # logs, does not raise
    await _session(_db_call_id=[None])._record_current_agent("Front desk")    # no call row yet: nothing to do
    await _session(_db_call_id=[1])._record_current_agent("")                  # no name: nothing to do


async def test_a_new_call_row_is_not_given_another_agent_s_name(world, broadcasts, monkeypatch):
    """The row is made when the stream starts, before anyone knows which agent will answer, so it names none."""
    async def nothing(*a, **k):
        return None

    async def no_greeting():
        return None

    monkeypatch.setattr(outbound_api, "retrieve_outbound_context", nothing)
    monkeypatch.setattr(media_bridge.event_logger, "emit", nothing)
    session = _session(_stream_sid=[None], _call_sid=[None], _org_suspended=False, _chat_history=[], _send_greeting=no_greeting,
                       _adopt_outbound_context=nothing, _tenant_id=world.tenants["acme"])
    await session._on_stream_start({"start": {"streamSid": "ST-1", "callSid": "CA-LABEL-1", "from": "+919876500001", "to": "+918000000099"}})
    if session._injection_task:
        session._injection_task.cancel()
    media_bridge.ACTIVE_CALL_QUEUES.pop("+919876500001", None)
    await asyncio.sleep(0)

    async with world.maker() as db:
        call = await db.get(Call, session._db_call_id[0])
        assert call.call_sid == "CA-LABEL-1" and call.live_status == "in-progress"
        assert not call.current_agent
    created = [m for m in broadcasts if m["type"] == "call_created"]
    assert created and not created[0]["call"]["current_agent"]
