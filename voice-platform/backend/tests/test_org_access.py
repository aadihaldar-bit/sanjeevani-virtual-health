import pytest
from sqlalchemy import select

from org_env import ADMIN, ADMIN2, LEGACY, env  # noqa: F401  (env is a fixture)
from app.models import TenantMembership

pytestmark = pytest.mark.asyncio


async def test_platform_admins_come_from_configuration_and_see_everything(env):
    me = (await env.get("/api/me", user=ADMIN)).json()
    assert me["role"] == "super_admin" and me["is_super_admin"] and me["capabilities"]["allocate_numbers"]
    assert (await env.get("/api/me", user=ADMIN2)).json()["role"] == "super_admin"


async def test_requests_without_sign_in_are_refused(env):
    assert (await env.client.get("/api/me")).status_code == 401
    assert (await env.client.get("/api/tenants")).status_code == 401


async def test_a_signed_in_user_with_no_organization_is_a_plain_member(env):
    me = (await env.get("/api/me", user=LEGACY)).json()
    assert me["role"] == "operator" and me["tenant_id"] is None and not me["capabilities"]["manage_members"]
    assert (await env.get("/api/tenants", user=LEGACY)).json() == {"orgs": []}


async def test_only_platform_admins_create_and_change_organizations(env):
    org = await env.make_org("Acme Corp", admin_email="boss@acme.test")
    assert org["slug"] == "acme-corp" and org["status"] == "active"
    assert (await env.post("/api/tenants", user="boss@acme.test", json={"name": "Rival"})).status_code == 403
    assert (await env.patch(f"/api/tenants/{org['id']}", user="boss@acme.test", json={"status": "suspended"})).status_code == 403
    changed = (await env.patch(f"/api/tenants/{org['id']}", json={"status": "suspended"})).json()
    assert changed["status"] == "suspended"


async def test_duplicate_organization_names_are_refused(env):
    await env.make_org("Acme Corp")
    assert (await env.post("/api/tenants", json={"name": "Acme Corp"})).status_code == 409


async def test_organization_admin_manages_only_their_own_members(env):
    acme = await env.make_org("Acme", admin_email="boss@acme.test")
    globex = await env.make_org("Globex", admin_email="boss@globex.test")
    ok = await env.post(f"/api/tenants/{acme['id']}/members", user="boss@acme.test", json={"email": "Sam@Acme.test", "role": "operator"})
    assert ok.status_code == 201 and ok.json()["email"] == "sam@acme.test" and ok.json()["status"] == "invited"
    assert (await env.get(f"/api/tenants/{globex['id']}/members", user="boss@acme.test")).status_code == 403
    assert (await env.post(f"/api/tenants/{globex['id']}/members", user="boss@acme.test", json={"email": "x@y.test"})).status_code == 403


async def test_a_member_cannot_manage_anything(env):
    acme = await env.make_org("Acme", admin_email="boss@acme.test", member_email="sam@acme.test")
    assert (await env.get(f"/api/tenants/{acme['id']}/members", user="sam@acme.test")).status_code == 403
    assert (await env.post(f"/api/tenants/{acme['id']}/members", user="sam@acme.test", json={"email": "z@acme.test"})).status_code == 403
    me = (await env.get("/api/me", user="sam@acme.test")).json()
    assert me["role"] == "operator" and me["tenant_name"] == "Acme" and not me["capabilities"]["manage_numbers"]


async def test_invitation_becomes_active_on_first_sign_in(env):
    acme = await env.make_org("Acme", admin_email="boss@acme.test", member_email="sam@acme.test")
    async with env.maker() as db:
        assert (await db.execute(select(TenantMembership.status).where(TenantMembership.email == "sam@acme.test"))).scalar() == "invited"
    await env.get("/api/me", user="sam@acme.test")
    async with env.maker() as db:
        assert (await db.execute(select(TenantMembership.status).where(TenantMembership.email == "sam@acme.test"))).scalar() == "active"


async def test_a_person_belongs_to_one_organization_and_platform_admins_need_none(env):
    acme = await env.make_org("Acme")
    globex = await env.make_org("Globex")
    assert (await env.post(f"/api/tenants/{acme['id']}/members", json={"email": "sam@x.test"})).status_code == 201
    assert (await env.post(f"/api/tenants/{globex['id']}/members", json={"email": "sam@x.test"})).status_code == 409
    assert (await env.post(f"/api/tenants/{acme['id']}/members", json={"email": ADMIN})).status_code == 409
    assert (await env.post(f"/api/tenants/{acme['id']}/members", json={"email": "not-an-email"})).status_code == 422


async def test_an_organization_always_keeps_one_administrator(env):
    acme = await env.make_org("Acme", admin_email="boss@acme.test", member_email="sam@acme.test")
    members = (await env.get(f"/api/tenants/{acme['id']}/members")).json()["members"]
    boss = next(m for m in members if m["email"] == "boss@acme.test")
    assert (await env.patch(f"/api/tenants/{acme['id']}/members/{boss['id']}", json={"role": "operator"})).status_code == 409
    assert (await env.delete(f"/api/tenants/{acme['id']}/members/{boss['id']}")).status_code == 409
    sam = next(m for m in members if m["email"] == "sam@acme.test")
    assert (await env.patch(f"/api/tenants/{acme['id']}/members/{sam['id']}", json={"role": "tenant_admin"})).status_code == 200
    assert (await env.delete(f"/api/tenants/{acme['id']}/members/{boss['id']}")).status_code == 200


async def test_suspended_organizations_lose_their_admin_rights(env):
    acme = await env.make_org("Acme", admin_email="boss@acme.test")
    await env.patch(f"/api/tenants/{acme['id']}", json={"status": "suspended"})
    assert (await env.get(f"/api/tenants/{acme['id']}/members", user="boss@acme.test")).status_code == 403


async def test_audit_trail_records_actions_and_is_scoped(env):
    acme = await env.make_org("Acme", admin_email="boss@acme.test")
    globex = await env.make_org("Globex", admin_email="boss@globex.test")
    everything = (await env.get("/api/admin/audit")).json()["entries"]
    assert {"org.create", "member.invite"} <= {e["action"] for e in everything}
    mine = (await env.get("/api/admin/audit", user="boss@acme.test")).json()["entries"]
    assert mine and all(e["tenant_id"] == acme["id"] for e in mine)
    assert (await env.get(f"/api/admin/audit?tenant_id={globex['id']}", user="boss@acme.test")).status_code == 403
    assert (await env.get("/api/admin/audit", user=LEGACY)).status_code == 403


async def test_listing_organizations_is_scoped_to_the_caller(env):
    await env.make_org("Acme", admin_email="boss@acme.test")
    await env.make_org("Globex", admin_email="boss@globex.test")
    assert len((await env.get("/api/tenants")).json()["orgs"]) == 2
    own = (await env.get("/api/tenants", user="boss@acme.test")).json()["orgs"]
    assert [o["name"] for o in own] == ["Acme"]


async def test_api_clients_can_be_attached_to_an_organization_by_platform_admins(env):
    from app.models import APIClient
    acme = await env.make_org("Acme", admin_email="boss@acme.test")
    async with env.maker() as db:
        db.add(APIClient(client_name="Acme monitor", api_key_hash="h" * 64, key_prefix="stl_out"))
        await db.commit()
        client_id = (await db.execute(select(APIClient.id))).scalar()
    assert (await env.put(f"/api/admin/api-clients/{client_id}/tenant", user="boss@acme.test", json={"tenant_id": acme["id"]})).status_code == 403
    assert (await env.put(f"/api/admin/api-clients/{client_id}/tenant", json={"tenant_id": acme["id"]})).json()["tenant_id"] == acme["id"]
    assert (await env.put(f"/api/admin/api-clients/{client_id}/tenant", json={"tenant_id": 999})).status_code == 404
    mine = (await env.get("/api/admin/api-clients-by-tenant", user="boss@acme.test")).json()["clients"]
    assert [c["name"] for c in mine] == ["Acme monitor"]
    assert (await env.get("/api/admin/api-clients-by-tenant")).json()["clients"][0]["tenant_id"] == acme["id"]
