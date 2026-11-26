import pytest
from sqlalchemy import select

from org_env import ADMIN, env  # noqa: F401
from app import numbers as nums
from app.models import NumberAllocationEvent, Workflow

pytestmark = pytest.mark.asyncio


# --- normalisation (no database) ---------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("+918069879488", "+918069879488"),
    ("918069879488", "+918069879488"),
    ("8069879488", "+918069879488"),
    ("080 6987 9488", "+918069879488"),
    ("+91 (80) 6987-9488", "+918069879488"),
    ("00918069879488", "+918069879488"),
    ("+14155550123", "+14155550123"),
])
def test_numbers_are_normalised_to_e164(raw, expected):
    assert nums.normalize_number(raw) == expected


@pytest.mark.parametrize("raw", ["", "abc", "123", "+0123456789", "+9112", "1234567890123456789"])
def test_bad_numbers_are_refused(raw):
    with pytest.raises(nums.NumberError):
        nums.normalize_number(raw)


# --- inventory and allocation ----------------------------------------------------------------------

async def setup_two_orgs(env):
    acme = await env.make_org("Acme", admin_email="boss@acme.test", member_email="sam@acme.test")
    globex = await env.make_org("Globex", admin_email="boss@globex.test")
    async with env.maker() as db:
        db.add_all([Workflow(name="Acme desk", tenant_id=acme["id"]), Workflow(name="Globex desk", tenant_id=globex["id"]),
                    Workflow(name="Shared demo", tenant_id=None)])
        await db.commit()
        wfs = {w.name: w.id for w in (await db.execute(select(Workflow))).scalars().all()}
    return acme, globex, wfs


async def add(env, number="+918069879488", **kw):
    response = await env.post("/api/numbers", json={"number": number, **kw})
    assert response.status_code == 201, response.text
    return response.json()


async def test_only_platform_admins_add_and_allocate_numbers(env):
    acme, _, _ = await setup_two_orgs(env)
    assert (await env.post("/api/numbers", user="boss@acme.test", json={"number": "+918069879488"})).status_code == 403
    number = await add(env)
    assert number["status"] == "unallocated"
    assert (await env.post(f"/api/numbers/{number['id']}/allocate", user="boss@acme.test", json={"tenant_id": acme["id"]})).status_code == 403
    allocated = (await env.post(f"/api/numbers/{number['id']}/allocate", json={"tenant_id": acme["id"]})).json()
    assert allocated["tenant_id"] == acme["id"] and allocated["status"] == "allocated"


async def test_duplicates_and_bad_values_are_rejected_with_a_reason(env):
    await add(env)
    dup = await env.post("/api/numbers", json={"number": "918069879488"})
    assert dup.status_code == 409 and "already" in dup.json()["detail"]
    assert (await env.post("/api/numbers", json={"number": "abc"})).status_code == 400
    assert (await env.post("/api/numbers", json={"number": "+911234567890", "carrier": "pigeon"})).status_code == 400
    assert (await env.post("/api/numbers", json={"number": "+911234567890", "direction": "sideways"})).status_code == 400


async def test_organization_admin_assigns_only_their_numbers_to_their_agents(env):
    acme, globex, wfs = await setup_two_orgs(env)
    mine, theirs = await add(env, "+918069879488"), await add(env, "+918069879499")
    await env.post(f"/api/numbers/{mine['id']}/allocate", json={"tenant_id": acme["id"]})
    await env.post(f"/api/numbers/{theirs['id']}/allocate", json={"tenant_id": globex["id"]})

    ok = await env.post(f"/api/numbers/{mine['id']}/assign", user="boss@acme.test", json={"workflow_id": wfs["Acme desk"]})
    assert ok.status_code == 200 and ok.json()["workflow_id"] == wfs["Acme desk"] and ok.json()["status"] == "assigned"
    shared = await env.post(f"/api/numbers/{mine['id']}/assign", user="boss@acme.test", json={"workflow_id": wfs["Shared demo"]})
    assert shared.status_code == 200                                                      # a shared agent is fine
    other_agent = await env.post(f"/api/numbers/{mine['id']}/assign", user="boss@acme.test", json={"workflow_id": wfs["Globex desk"]})
    assert other_agent.status_code == 400 and "different organization" in other_agent.json()["detail"]
    others_number = await env.post(f"/api/numbers/{theirs['id']}/assign", user="boss@acme.test", json={"workflow_id": wfs["Acme desk"]})
    assert others_number.status_code == 404                                               # it does not exist for them
    assert (await env.post(f"/api/numbers/{mine['id']}/assign", user="sam@acme.test", json={"workflow_id": None})).status_code == 403


async def test_a_number_must_be_allocated_before_it_can_be_assigned(env):
    _, _, wfs = await setup_two_orgs(env)
    number = await add(env)
    response = await env.post(f"/api/numbers/{number['id']}/assign", json={"workflow_id": wfs["Shared demo"]})
    assert response.status_code == 400 and "Allocate" in response.json()["detail"]


async def test_moving_a_number_to_another_organization_clears_its_agent(env):
    acme, globex, wfs = await setup_two_orgs(env)
    number = await add(env, tenant_id=acme["id"])
    await env.post(f"/api/numbers/{number['id']}/assign", json={"workflow_id": wfs["Acme desk"]})
    moved = (await env.post(f"/api/numbers/{number['id']}/allocate", json={"tenant_id": globex["id"]})).json()
    assert moved["tenant_id"] == globex["id"] and moved["workflow_id"] is None
    released = (await env.post(f"/api/numbers/{number['id']}/release")).json()
    assert released["tenant_id"] is None and released["status"] == "unallocated"


async def test_suspended_organizations_cannot_receive_numbers(env):
    acme, _, _ = await setup_two_orgs(env)
    await env.patch(f"/api/tenants/{acme['id']}", json={"status": "suspended"})
    number = await add(env)
    response = await env.post(f"/api/numbers/{number['id']}/allocate", json={"tenant_id": acme["id"]})
    assert response.status_code == 400 and "suspended" in response.json()["detail"]


async def test_each_organization_sees_only_its_own_numbers(env):
    acme, globex, _ = await setup_two_orgs(env)
    await add(env, "+918069879488", tenant_id=acme["id"])
    await add(env, "+918069879499", tenant_id=globex["id"])
    await add(env, "+918069879500")
    assert len((await env.get("/api/numbers")).json()["numbers"]) == 3
    assert len((await env.get("/api/numbers?unallocated=true")).json()["numbers"]) == 1
    mine = (await env.get("/api/numbers", user="boss@acme.test")).json()["numbers"]
    assert [n["number"] for n in mine] == ["+918069879488"]
    assert [n["number"] for n in (await env.get("/api/numbers", user="sam@acme.test")).json()["numbers"]] == ["+918069879488"]
    assert (await env.get("/api/numbers", user="nobody@x.test")).json()["numbers"] == []


async def test_every_change_is_written_to_the_history(env):
    acme, _, wfs = await setup_two_orgs(env)
    number = await add(env)
    await env.post(f"/api/numbers/{number['id']}/allocate", json={"tenant_id": acme["id"]})
    await env.post(f"/api/numbers/{number['id']}/assign", user="boss@acme.test", json={"workflow_id": wfs["Acme desk"]})
    await env.post(f"/api/numbers/{number['id']}/assign", user="boss@acme.test", json={"workflow_id": None})
    await env.post(f"/api/numbers/{number['id']}/release")
    history = (await env.get(f"/api/numbers/{number['id']}/history")).json()["events"]
    assert [e["action"] for e in reversed(history)] == ["added", "allocated", "assigned", "unassigned", "released"]
    assert history[0]["actor"] == ADMIN


async def test_csv_import_reports_accepted_and_rejected_rows(env):
    csv_text = "number,carrier,direction,label\n+918069879488,smartflo,both,Main line\n918069879488,twilio,both,dup\nnope,smartflo,both,bad\n+14155550123,twilio,outbound,US out\n"
    result = (await env.post("/api/numbers/import", json={"csv": csv_text})).json()
    assert result["accepted"] == ["+918069879488", "+14155550123"]
    assert [r["row"] for r in result["rejected"]] == [2, 3] and "already" in result["rejected"][0]["reason"]
    assert len((await env.get("/api/numbers")).json()["numbers"]) == 2


async def test_workflow_ownership_can_be_set_but_not_while_other_orgs_use_it(env):
    acme, globex, wfs = await setup_two_orgs(env)
    number = await add(env, tenant_id=globex["id"])
    await env.post(f"/api/numbers/{number['id']}/assign", json={"workflow_id": wfs["Shared demo"]})
    refused = await env.put(f"/api/workflows/{wfs['Shared demo']}/tenant", json={"tenant_id": acme["id"]})
    assert refused.status_code == 409
    await env.post(f"/api/numbers/{number['id']}/assign", json={"workflow_id": None})
    assert (await env.put(f"/api/workflows/{wfs['Shared demo']}/tenant", json={"tenant_id": acme["id"]})).json()["tenant_id"] == acme["id"]
    assert (await env.put(f"/api/workflows/{wfs['Shared demo']}/tenant", user="boss@acme.test", json={"tenant_id": None})).status_code == 403


async def test_agents_offered_for_assignment_are_the_organizations_own_plus_shared(env):
    acme, _, _ = await setup_two_orgs(env)
    names = {w["name"] for w in (await env.get("/api/tenant-workflows", user="boss@acme.test")).json()["workflows"]}
    assert names == {"Acme desk", "Shared demo"}


# --- inbound routing and caller id (logic) --------------------------------------------------------

async def test_inbound_calls_route_by_dialled_number_to_organization_and_agent(env):
    acme, globex, wfs = await setup_two_orgs(env)
    a = await add(env, "+918069879488", tenant_id=acme["id"])
    b = await add(env, "+918069879499", tenant_id=globex["id"])
    await env.post(f"/api/numbers/{a['id']}/assign", json={"workflow_id": wfs["Acme desk"]})
    await env.post(f"/api/numbers/{b['id']}/assign", json={"workflow_id": wfs["Globex desk"]})
    async with env.maker() as db:
        r1 = await nums.resolve_inbound(db, "918069879488")                # the carrier may omit the plus
        r2 = await nums.resolve_inbound(db, "+918069879499")
        unknown = await nums.resolve_inbound(db, "+919999999999")
        junk = await nums.resolve_inbound(db, "not a number")
        none = await nums.resolve_inbound(db, None)
    assert (r1.found, r1.tenant_id, r1.workflow_id) == (True, acme["id"], wfs["Acme desk"])
    assert (r2.found, r2.tenant_id, r2.workflow_id) == (True, globex["id"], wfs["Globex desk"])
    assert not unknown.found and not junk.found and not none.found                 # keep today's behaviour for unknown numbers


async def test_a_number_with_no_agent_is_answered_by_its_own_organizations_live_agent(env):
    acme, globex, wfs = await setup_two_orgs(env)
    await add(env, "+918069879488", tenant_id=acme["id"])
    async with env.maker() as db:
        for name in ("Acme desk", "Globex desk"):
            (await db.get(Workflow, wfs[name])).is_active = True                   # each organization has its own live agent
        await db.commit()
        route = await nums.resolve_inbound(db, "+918069879488")
    assert (route.found, route.tenant_id, route.workflow_id) == (True, acme["id"], wfs["Acme desk"])


async def test_a_number_with_no_agent_never_borrows_another_organizations_agent(env):
    acme, globex, wfs = await setup_two_orgs(env)
    await add(env, "+918069879488", tenant_id=acme["id"])
    async with env.maker() as db:
        (await db.get(Workflow, wfs["Globex desk"])).is_active = True               # only Globex has a live agent
        await db.commit()
        route = await nums.resolve_inbound(db, "+918069879488")
    assert route.found and route.tenant_id == acme["id"] and route.workflow_id is None


async def test_a_number_given_an_agent_reaches_it_even_when_another_agent_is_live(env):
    acme, _, wfs = await setup_two_orgs(env)
    number = await add(env, "+918069879488", tenant_id=acme["id"])
    async with env.maker() as db:
        db.add(Workflow(name="Acme second", tenant_id=acme["id"], is_active=True))
        await db.commit()
    await env.post(f"/api/numbers/{number['id']}/assign", json={"workflow_id": wfs["Acme desk"]})     # assigned, and not the live one
    async with env.maker() as db:
        route = await nums.resolve_inbound(db, "+918069879488")
    assert route.workflow_id == wfs["Acme desk"]


async def test_outbound_only_numbers_do_not_answer_inbound_calls(env):
    acme, _, _ = await setup_two_orgs(env)
    await add(env, "+918069879488", tenant_id=acme["id"], direction="outbound")
    async with env.maker() as db:
        assert not (await nums.resolve_inbound(db, "+918069879488")).found


async def test_a_suspended_organization_is_flagged_on_inbound(env):
    acme, _, _ = await setup_two_orgs(env)
    await add(env, "+918069879488", tenant_id=acme["id"])
    await env.patch(f"/api/tenants/{acme['id']}", json={"status": "suspended"})
    async with env.maker() as db:
        route = await nums.resolve_inbound(db, "+918069879488")
    assert route.found and route.org_suspended


async def test_caller_id_prefers_the_agents_own_number_then_any_outbound_number(env):
    acme, globex, wfs = await setup_two_orgs(env)
    inbound_only = await add(env, "+918069879400", tenant_id=acme["id"], direction="inbound")
    general = await add(env, "+918069879401", tenant_id=acme["id"], direction="both")
    desk = await add(env, "+918069879402", tenant_id=acme["id"], direction="outbound")
    await env.post(f"/api/numbers/{desk['id']}/assign", json={"workflow_id": wfs["Acme desk"]})
    async with env.maker() as db:
        assert await nums.pick_caller_id(db, acme["id"], wfs["Acme desk"]) == "+918069879402"
        assert await nums.pick_caller_id(db, acme["id"], None) == "+918069879401"
        assert await nums.pick_caller_id(db, globex["id"]) is None                  # no outbound number: caller must be told
        assert await nums.pick_caller_id(db, None) is None
