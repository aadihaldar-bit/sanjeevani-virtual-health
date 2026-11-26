"""One tenant must never see, change or hear another tenant's data. These tests run the real data routers behind the real
tenant dependency, with two customers (Acme, Globex) and STL itself, and check every kind of read and write."""
import io
import json
import os
from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy import select

import httpx

from org_env import ADMIN, ADMIN2, LEGACY, build_app, env  # noqa: F401
from app import knowledge_routes, tenancy
from app.models import Call, CallStatus, Escalation, Event, Tenant, Ticket, TranscriptMessage, Workflow

pytestmark = pytest.mark.asyncio

ACME_BOSS, GLOBEX_BOSS = "boss@acme.test", "boss@globex.test"


async def _noop(*a, **k):
    return None


@pytest_asyncio.fixture
async def world(env, monkeypatch, tmp_path):
    """STL + Acme + Globex, each with calls, tickets, an escalation, events, a transcript and a workflow."""
    monkeypatch.setenv("MULTI_TENANT_ENABLED", "true")
    async with env.maker() as db:
        stl = Tenant(name="STL Digital", slug="stl", is_system=True)
        db.add(stl)
        await db.commit()
        tenancy.set_system_tenant_id(stl.id)
        stl_id = stl.id
    acme = await env.make_org("Acme", admin_email=ACME_BOSS)
    globex = await env.make_org("Globex", admin_email=GLOBEX_BOSS)

    ids = {}
    async with env.maker() as db:
        for name, tid in (("stl", stl_id), ("acme", acme["id"]), ("globex", globex["id"])):
            for n, status in enumerate((CallStatus.ACTIVE, CallStatus.COMPLETED)):
                call = Call(tenant_id=tid, conversation_id=f"{name}-{n}", caller_number=f"+9190000{tid}{n}", status=status,
                            started_at=datetime.utcnow(), intent=f"{name}-intent", sentiment="positive", is_escalated=(n == 1))
                db.add(call)
                await db.flush()
                ids[f"{name}{n}"] = call.id
                db.add(TranscriptMessage(call_id=call.id, role="user", message=f"secret of {name} {n}", sequence_number=1))
                db.add(Event(call_id=call.id, event_type=f"evt-{name}"))
                if n == 1:
                    db.add(Escalation(call_id=call.id, reason=f"{name} escalation"))
                    db.add(Ticket(tenant_id=tid, ticket_id=f"T-{name}", call_id=call.id, caller_number="+911", intent="i",
                                  issue=f"{name} issue", priority="high", transcript_summary="s"))
            wf = Workflow(tenant_id=tid, name=f"{name} desk", is_active=True)
            db.add(wf)
            await db.flush()
            ids[f"wf_{name}"] = wf.id
        await db.commit()

    # the real data routers, behind the same dependencies main.py uses
    env.app = build_app(env.maker, tenant_data=True)
    env.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app), base_url="http://test")

    from app.rag_engine import LocalHybridRAGEngine
    engine = LocalHybridRAGEngine(kb_dir=str(tmp_path / "kb"))
    monkeypatch.setattr(knowledge_routes, "rag_engine", engine)
    monkeypatch.setattr(knowledge_routes.dashboard_manager, "broadcast", _noop)
    env.engine = engine
    env.ids = ids
    env.tenants = {"stl": stl_id, "acme": acme["id"], "globex": globex["id"]}
    yield env
    await env.client.aclose()


async def get_with(world, user, path, **headers):
    return await world.client.get(path, headers={**world.as_user(user), **headers})


def calls_of(res):
    assert res.status_code == 200, res.text
    return {c["conversation_id"] for c in res.json()["calls"]}


# --- reads -------------------------------------------------------------------------------------------------------------

async def test_each_tenant_lists_only_its_own_calls(world):
    assert calls_of(await world.get("/api/calls", user=ACME_BOSS)) == {"acme-0", "acme-1"}
    assert calls_of(await world.get("/api/calls", user=GLOBEX_BOSS)) == {"globex-0", "globex-1"}
    assert (await world.get("/api/calls", user=ACME_BOSS)).json()["total"] == 2            # the count is theirs too


async def test_active_calls_and_the_live_roster_are_limited(world):
    res = await world.get("/api/calls/active", user=ACME_BOSS)
    assert [c["conversation_id"] for c in res.json()["calls"]] == ["acme-0"] and res.json()["count"] == 1


async def test_another_tenants_call_looks_like_it_does_not_exist(world):
    other = world.ids["globex0"]
    for path in (f"/api/calls/{other}", f"/api/calls/{other}/transcript", f"/api/health/calls/{other}"):
        assert (await world.get(path, user=ACME_BOSS)).status_code == 404, path
    assert (await world.post(f"/api/calls/{other}/end", user=ACME_BOSS)).status_code == 404
    assert (await world.get(f"/api/calls/{world.ids['acme0']}", user=ACME_BOSS)).status_code == 200


async def test_transcripts_never_cross_tenants(world):
    text = (await world.get(f"/api/calls/{world.ids['acme0']}", user=ACME_BOSS)).text
    assert "secret of acme 0" in text and "globex" not in text and "secret of stl" not in text


async def test_tickets_escalations_and_events_are_limited(world):
    tickets = (await world.get("/api/tickets", user=ACME_BOSS)).json()
    assert [t["ticket_id"] for t in tickets] == ["T-acme"]
    esc = (await world.get("/api/escalations", user=ACME_BOSS)).json()
    assert [e["reason"] for e in esc["escalations"]] == ["acme escalation"]
    assert (await world.get("/api/escalations/metrics", user=ACME_BOSS)).json()["total_escalations"] == 1
    events = (await world.get("/api/events", user=ACME_BOSS)).json()["events"]
    assert {e["event_type"] for e in events} == {"evt-acme"}


async def test_analytics_only_count_the_callers_tenant(world):
    mine = (await world.get("/api/analytics/summary", user=ACME_BOSS)).json()
    assert mine["total_calls"] == 2 and mine["active_calls"] == 1 and mine["escalated_calls"] == 1
    trends = (await world.get("/api/analytics/trends", user=GLOBEX_BOSS)).json()
    assert {i["name"] for i in trends["intents"]} == {"globex-intent"}
    daily = (await world.get("/api/analytics/daily?days=1", user=ACME_BOSS)).json()["data"]
    assert daily[-1]["total_calls"] == 2


async def test_workflows_and_agents_are_limited(world):
    listing = (await world.get("/api/workflows/", user=ACME_BOSS)).json()
    assert {w["name"] for w in listing} == {"acme desk"}
    assert (await world.get(f"/api/workflows/{world.ids['wf_globex']}", user=ACME_BOSS)).status_code == 404
    agents = (await world.get("/api/agents/", user=GLOBEX_BOSS)).json()
    names = {a["name"] for a in (agents["agents"] if isinstance(agents, dict) else agents)}
    assert names == {"globex desk"}


# --- who sees what ---------------------------------------------------------------------------------------------------

async def test_a_super_admin_sees_every_tenant_and_can_look_at_one(world):
    assert calls_of(await world.get("/api/calls", user=ADMIN)) == {f"{t}-{n}" for t in ("stl", "acme", "globex") for n in (0, 1)}
    one = await get_with(world, ADMIN, "/api/calls", **{"x-tenant-id": str(world.tenants["acme"])})
    assert calls_of(one) == {"acme-0", "acme-1"}
    assert (await get_with(world, ADMIN, "/api/calls", **{"x-tenant-id": "9999"})).status_code == 404
    assert calls_of(await world.get("/api/calls", user=ADMIN2)) >= {"acme-0", "globex-0"}


async def test_the_header_gives_an_ordinary_user_no_extra_reach(world):
    res = await get_with(world, ACME_BOSS, "/api/calls", **{"x-tenant-id": str(world.tenants["globex"])})
    assert calls_of(res) == {"acme-0", "acme-1"}


async def test_stl_staff_without_a_membership_see_stls_data_only(world):
    assert calls_of(await world.get("/api/calls", user=LEGACY)) == {"stl-0", "stl-1"}


async def test_a_suspended_tenant_is_locked_out(world):
    await world.patch(f"/api/tenants/{world.tenants['acme']}", json={"status": "suspended"})
    assert (await world.get("/api/calls", user=ACME_BOSS)).status_code == 403


async def test_platform_routes_are_for_staff_only(world):
    for path in ("/api/license/status", "/api/license/registration-status"):
        assert (await world.get(path, user=ACME_BOSS)).status_code == 403, path
        assert (await world.get(path, user=LEGACY)).status_code != 403, path
    assert (await world.post("/api/sync", user=ACME_BOSS)).status_code == 403
    assert (await world.post("/api/calls/outbound", user=ACME_BOSS, json={"to_number": "+919800000000"})).status_code == 403


# --- writes ----------------------------------------------------------------------------------------------------------

async def test_activating_an_agent_only_affects_its_own_tenant(world):
    async with world.maker() as db:
        db.add(Workflow(tenant_id=world.tenants["acme"], name="acme second", is_active=False))
        await db.commit()
        second = (await db.execute(select(Workflow.id).where(Workflow.name == "acme second"))).scalar()
    assert (await world.post(f"/api/agents/{second}/activate", user=ACME_BOSS)).status_code == 200
    async with world.maker() as db:
        active = {w.name for w in (await db.execute(select(Workflow).where(Workflow.is_active.is_(True)))).scalars().all()}
    assert active == {"stl desk", "globex desk", "acme second"}                 # the others kept theirs


async def test_a_tenant_cannot_activate_or_delete_anothers_agent(world):
    other = world.ids["wf_globex"]
    assert (await world.post(f"/api/agents/{other}/activate", user=ACME_BOSS)).status_code == 404
    assert (await world.delete(f"/api/agents/{other}", user=ACME_BOSS)).status_code in (403, 404)
    assert (await world.post(f"/api/workflows/{other}/activate", user=ACME_BOSS)).status_code == 404
    async with world.maker() as db:
        assert await db.get(Workflow, other) is not None


async def test_what_a_tenant_creates_belongs_to_that_tenant(world):
    res = await world.post("/api/agents/", user=ACME_BOSS, json={"name": "New desk", "description": "d"})
    assert res.status_code in (200, 201), res.text
    async with world.maker() as db:
        row = (await db.execute(select(Workflow).where(Workflow.name == "New desk"))).scalars().first()
        assert row.tenant_id == world.tenants["acme"]
    assert "New desk" not in (await world.get("/api/workflows/", user=GLOBEX_BOSS)).text


async def test_rows_made_outside_a_request_still_get_a_tenant(world):
    async with world.maker() as db:
        call = Call(conversation_id="phone-1")
        db.add(call)
        await db.flush()
        ticket = Ticket(ticket_id="T-x", call_id=call.id, caller_number="+9", intent="i", issue="x", priority="p", transcript_summary="s")
        db.add(ticket)
        await db.commit()
        assert call.tenant_id == world.tenants["stl"]                              # default: the platform's own
        assert ticket.tenant_id == world.tenants["stl"]
        owned = Call(conversation_id="phone-2", tenant_id=world.tenants["acme"])
        db.add(owned)
        await db.flush()
        t2 = Ticket(ticket_id="T-y", call_id=owned.id, caller_number="+9", intent="i", issue="y", priority="p", transcript_summary="s")
        db.add(t2)
        await db.commit()
        assert t2.tenant_id == world.tenants["acme"]                               # a ticket follows its call


# --- switch off ------------------------------------------------------------------------------------------------------

async def test_with_multi_tenancy_off_staff_see_everything_and_tenant_users_nothing(world, monkeypatch):
    monkeypatch.setenv("MULTI_TENANT_ENABLED", "false")
    assert len(calls_of(await world.get("/api/calls", user=LEGACY))) == 6          # exactly as before the feature
    assert (await world.get("/api/calls", user=ACME_BOSS)).status_code == 403      # a tenant user must not read the platform data
    assert (await world.get("/api/tickets", user=ACME_BOSS)).status_code == 403


# --- knowledge base --------------------------------------------------------------------------------------------------

async def upload(env, user, name, text):
    return await env.client.post("/api/knowledge/upload", headers=env.as_user(user),
                                 files={"file": (name, io.BytesIO(text.encode()), "text/plain")})


async def test_knowledge_documents_are_private_to_their_tenant(world):
    a = await upload(world, ACME_BOSS, "acme.md", "# Acme\n\nThe Acme refund window is thirty days for orders.")
    g = await upload(world, GLOBEX_BOSS, "globex.md", "# Globex\n\nThe Globex refund window is ninety days for orders.")
    assert a.status_code == 200 and g.status_code == 200
    assert [d["name"] for d in (await world.get("/api/knowledge/documents", user=ACME_BOSS)).json()["documents"]] == ["acme.md"]
    assert [d["name"] for d in (await world.get("/api/knowledge/documents", user=GLOBEX_BOSS)).json()["documents"]] == ["globex.md"]
    assert {d["name"] for d in (await world.get("/api/knowledge/documents", user=ADMIN)).json()["documents"]} == {"acme.md", "globex.md"}


async def test_a_tenants_questions_are_answered_only_from_its_own_documents(world):
    await upload(world, ACME_BOSS, "acme.md", "# Acme\n\nThe Acme refund window is thirty days for orders.")
    await upload(world, GLOBEX_BOSS, "globex.md", "# Globex\n\nThe Globex refund window is ninety days for orders.")
    res = await world.post("/api/knowledge/search", user=ACME_BOSS, json={"query": "what is the refund window for orders"})
    texts = " ".join(r["text"] for r in res.json()["results"])
    assert "Acme" in texts and "Globex" not in texts
    # and the engine itself (the path a live call takes) holds the same line
    found = await world.engine.search_ranked("refund window for orders", tenant_id=world.tenants["globex"])
    assert found["results"] and all("Acme" not in r["text"] for r in found["results"])


async def test_one_tenant_cannot_delete_anothers_document_and_old_documents_belong_to_stl(world):
    a = (await upload(world, ACME_BOSS, "acme.md", "# Acme\n\nAcme private note about warranty.")).json()["document_id"]
    assert (await world.delete(f"/api/knowledge/documents/{a}", user=GLOBEX_BOSS)).status_code == 404
    # a document with no owner on record (built in, or uploaded before tenants) belongs to STL
    with open(os.path.join(world.engine.kb_dir, "legacy_policy.md"), "w", encoding="utf-8") as f:
        f.write("# Legacy policy\n\nThe legacy policy covers every STL staff member.")
    world.engine._load_all_documents()
    assert "legacy_policy.md" not in {d["name"] for d in (await world.get("/api/knowledge/documents", user=ACME_BOSS)).json()["documents"]}
    assert "legacy_policy.md" in {d["name"] for d in (await world.get("/api/knowledge/documents", user=LEGACY)).json()["documents"]}
    assert (await world.delete(f"/api/knowledge/documents/{a}", user=ACME_BOSS)).status_code == 200


async def test_rebuilding_the_platform_index_is_not_a_tenants_job(world):
    assert (await world.post("/api/knowledge/reindex", user=ACME_BOSS)).status_code == 403


# --- live feed -------------------------------------------------------------------------------------------------------

class FakeSocket:
    def __init__(self):
        self.received = []

    async def send_text(self, text):
        self.received.append(json.loads(text))


async def test_the_live_feed_only_delivers_a_tenants_own_calls(world):
    from app.websocket_manager import ConnectionManager
    mgr = ConnectionManager()
    mgr.listen_task = object()                                   # no Redis listener in tests
    sockets = {name: FakeSocket() for name in ("acme", "globex", "staff")}
    await mgr.connect(sockets["acme"], "a", tenant_id=world.tenants["acme"])
    await mgr.connect(sockets["globex"], "g", tenant_id=world.tenants["globex"])
    await mgr.connect(sockets["staff"], "s", tenant_id=None)     # a super admin watching everything

    await mgr.broadcast({"type": "call_updated", "call_id": world.ids["acme0"], "status": "active"})
    await mgr.broadcast({"type": "call_updated", "conversation_id": "globex-0", "status": "completed"})
    await mgr.broadcast({"type": "mystery"})                      # cannot be placed on a tenant: only the unrestricted see it

    got = {name: [m.get("call_id") or m.get("conversation_id") or m["type"] for m in s.received] for name, s in sockets.items()}
    assert got["acme"] == [world.ids["acme0"]]
    assert got["globex"] == ["globex-0"]
    assert got["staff"] == [world.ids["acme0"], "globex-0", "mystery"]


async def test_a_call_made_for_an_api_client_belongs_to_the_clients_tenant(world):
    from app.models import APIClient
    async with world.maker() as db:
        client = APIClient(client_name="Acme bot", api_key_hash="h1", key_prefix="stl", tenant_id=world.tenants["acme"])
        db.add(client)
        await db.flush()
        call = Call(conversation_id="api-1", api_client_id=client.id)
        db.add(call)
        await db.commit()
        assert call.tenant_id == world.tenants["acme"]


async def test_the_platforms_own_tenant_cannot_be_suspended_and_its_super_admins_are_fixed(world):
    stl = world.tenants["stl"]
    assert (await world.patch(f"/api/tenants/{stl}", json={"status": "suspended"})).status_code == 409
    async with world.maker() as db:
        from app.models import TenantMembership
        row = TenantMembership(tenant_id=stl, email="root@stl.test", role="super_admin")
        db.add(row)
        await db.commit()
        member_id = row.id
    assert (await world.patch(f"/api/tenants/{stl}/members/{member_id}", json={"role": "viewer"})).status_code == 409
    assert (await world.delete(f"/api/tenants/{stl}/members/{member_id}")).status_code == 409


async def test_knowledge_status_counts_only_the_callers_documents(world):
    await upload(world, ACME_BOSS, "acme.md", "# Acme\n\nAcme refund text for the status count.")
    await upload(world, GLOBEX_BOSS, "globex.md", "# Globex\n\nGlobex refund text for the status count.")
    world.engine._semantic_docs.update({d["id"] for d in world.engine.list_documents()})          # pretend both are embedded
    mine = (await world.get("/api/knowledge/status", user=ACME_BOSS)).json()
    assert mine["documents"] == 1 and mine["semantic_documents"] == 1                            # not the platform-wide totals
    assert (await world.get("/api/knowledge/status", user=ADMIN)).json()["documents"] == 2
