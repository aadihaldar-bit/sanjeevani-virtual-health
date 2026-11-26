"""What each role may do on the data routes. Viewers read; operators and managers do day-to-day work; only administrators
(and STL staff / super admins) change agents, knowledge, API keys. Writes are refused by default."""
import io

import pytest
import pytest_asyncio

from org_env import ADMIN, LEGACY, env  # noqa: F401
from test_tenant_isolation import ACME_BOSS, world  # noqa: F401
from app.models import Workflow
from app.tenancy import role_denial

pytestmark = pytest.mark.asyncio

VIEWER, OPERATOR, MANAGER = "vic@acme.test", "sam@acme.test", "max@acme.test"
STAFF_VIEWER = "ana@stl.test"             # a staff-domain person who was explicitly invited as a viewer


@pytest_asyncio.fixture
async def team(world):
    acme = world.tenants["acme"]
    for email, role in ((VIEWER, "viewer"), (OPERATOR, "operator"), (MANAGER, "manager")):
        assert (await world.post(f"/api/tenants/{acme}/members", json={"email": email, "role": role})).status_code == 201
    assert (await world.post(f"/api/tenants/{world.tenants['stl']}/members", json={"email": STAFF_VIEWER, "role": "viewer"})).status_code == 201
    return world


# --- the rule table itself -----------------------------------------------------------------------------------------------

READS = [("GET", "/api/calls"), ("GET", "/api/calls/5"), ("GET", "/api/tickets"), ("GET", "/api/analytics/summary"), ("GET", "/api/analytics/kpis"),
         ("GET", "/api/agents/"), ("GET", "/api/knowledge/documents"), ("POST", "/api/knowledge/search")]
OPERATE = [("POST", "/api/stt/transcribe"), ("POST", "/api/tts/synthesize"), ("POST", "/api/calls/5/end"), ("POST", "/api/calls/clear-stale"), ("POST", "/api/tickets/create"),
           ("POST", "/api/api/tickets/create"), ("POST", "/api/simulator/chat"), ("POST", "/api/simulator/voice-turn"),
           ("POST", "/api/simulator/end-call")]
ADMIN_ONLY = [("POST", "/api/agents/"), ("DELETE", "/api/agents/3"), ("POST", "/api/agents/3/activate"), ("POST", "/api/agents/3/chat"),
              ("POST", "/api/workflows/"), ("DELETE", "/api/workflows/3"), ("POST", "/api/workflows/3/activate"),
              ("POST", "/api/workflows/generate-ai"), ("POST", "/api/workflows/upload-kb"), ("POST", "/api/knowledge/upload"),
              ("DELETE", "/api/knowledge/documents/abc"), ("POST", "/api/calls/5/analyze"),
              ("POST", "/api/admin/api-clients"), ("POST", "/api/admin/api-clients/2/rotate-key"), ("DELETE", "/api/admin/api-clients/2"),
              ("GET", "/api/admin/api-clients"), ("POST", "/api/some/future/write")]


@pytest.mark.parametrize("role", ["viewer", "operator", "manager", "tenant_admin", "staff", "super_admin"])
def test_everyone_may_read(role):
    for method, path in READS:
        assert role_denial(role, method, path) is None, (role, method, path)


@pytest.mark.parametrize("role", ["operator", "manager", "tenant_admin", "staff", "super_admin"])
def test_day_to_day_work_is_open_to_operators_and_above(role):
    for method, path in OPERATE:
        assert role_denial(role, method, path) is None, (role, method, path)


def test_viewers_cannot_write_anything():
    for method, path in OPERATE + ADMIN_ONLY:
        if method == "GET":
            continue
        assert role_denial("viewer", method, path) == "Your role is read-only.", (method, path)


@pytest.mark.parametrize("role", ["operator", "manager"])
def test_operators_and_managers_cannot_change_agents_knowledge_or_keys(role):
    for method, path in ADMIN_ONLY:
        assert role_denial(role, method, path) == "Only an administrator can do that.", (method, path)


@pytest.mark.parametrize("role", ["tenant_admin", "staff", "super_admin"])
def test_administrators_staff_and_super_admins_can_do_everything(role):
    for method, path in ADMIN_ONLY + OPERATE + READS:
        assert role_denial(role, method, path) is None, (role, method, path)


def test_an_unknown_role_gets_no_more_than_a_viewer():
    assert role_denial("something-else", "POST", "/api/agents/") == "Your role is read-only."
    assert role_denial("something-else", "GET", "/api/calls") is None


# --- through the real routes ---------------------------------------------------------------------------------------------

async def test_a_viewer_reads_but_every_write_is_refused(team):
    assert (await team.get("/api/calls", user=VIEWER)).status_code == 200
    assert (await team.get("/api/knowledge/documents", user=VIEWER)).status_code == 200
    assert (await team.post("/api/knowledge/search", user=VIEWER, json={"query": "refund window"})).status_code == 200
    acme_wf = team.ids["wf_acme"]
    for method, path, body in (("post", "/api/agents/", {"name": "x", "description": "d"}),
                               ("post", f"/api/agents/{acme_wf}/activate", None),
                               ("delete", f"/api/agents/{acme_wf}", None),
                               ("post", "/api/admin/api-clients", {"client_name": "k"}),
                               ("post", f"/api/calls/{team.ids['acme0']}/end", None),
                               ("post", "/api/calls/clear-stale", None)):
        res = await getattr(team, method)(path, user=VIEWER, **({"json": body} if body is not None else {}))
        assert res.status_code == 403 and "read-only" in res.text, (path, res.status_code)
    async with team.maker() as db:
        assert await db.get(Workflow, acme_wf) is not None                    # nothing was deleted


async def test_an_operator_works_calls_but_cannot_touch_agents_knowledge_or_keys(team):
    assert (await team.post("/api/calls/clear-stale", user=OPERATOR)).status_code == 200
    assert (await team.post(f"/api/calls/{team.ids['acme0']}/end", user=OPERATOR)).status_code != 403
    for method, path, kwargs in (("post", "/api/agents/", {"json": {"name": "x", "description": "d"}}),
                                 ("delete", f"/api/agents/{team.ids['wf_acme']}", {}),
                                 ("post", "/api/workflows/generate-ai", {"json": {"prompt": "x"}}),
                                 ("post", f"/api/calls/{team.ids['acme0']}/analyze", {}),
                                 ("get", "/api/admin/api-clients", {}),
                                 ("post", "/api/admin/api-clients", {"json": {"client_name": "k"}}),
                                 ("post", "/api/knowledge/upload", {"files": {"file": ("a.md", io.BytesIO(b"# A\n\ntext"), "text/plain")}})):
        res = await getattr(team, method)(path, user=OPERATOR, **kwargs)
        assert res.status_code == 403 and "administrator" in res.text, (path, res.status_code, res.text[:80])


async def test_a_manager_has_the_same_reach_as_an_operator(team):
    assert (await team.post("/api/calls/clear-stale", user=MANAGER)).status_code == 200
    assert (await team.delete(f"/api/agents/{team.ids['wf_acme']}", user=MANAGER)).status_code == 403


async def test_an_administrator_changes_their_own_tenants_agents_and_knowledge(team):
    created = await team.post("/api/agents/", user=ACME_BOSS, json={"name": "Boss desk", "description": "d"})
    assert created.status_code in (200, 201)
    up = await team.client.post("/api/knowledge/upload", headers=team.as_user(ACME_BOSS),
                                files={"file": ("a.md", io.BytesIO(b"# Acme\n\nAcme note text here."), "text/plain")})
    assert up.status_code == 200
    assert (await team.get("/api/admin/api-clients", user=ACME_BOSS)).status_code == 200
    # an administrator still cannot reach another tenant's agent
    assert (await team.delete(f"/api/agents/{team.ids['wf_globex']}", user=ACME_BOSS)).status_code == 404


async def test_staff_and_super_admins_keep_full_access(team):
    assert (await team.post("/api/calls/clear-stale", user=LEGACY)).status_code == 200
    assert (await team.post("/api/agents/", user=LEGACY, json={"name": "Staff desk", "description": "d"})).status_code in (200, 201)
    assert (await team.post("/api/agents/", user=ADMIN, json={"name": "Admin desk", "description": "d"})).status_code in (200, 201)


async def test_an_explicit_membership_beats_a_staff_email_domain(team):
    """ana@stl.test passes the staff allow-list, but she was invited as a viewer: that is what she is."""
    assert (await team.get("/api/calls", user=STAFF_VIEWER)).status_code == 200
    assert (await team.post("/api/agents/", user=STAFF_VIEWER, json={"name": "x", "description": "d"})).status_code == 403
    for path in ("/api/license/status", "/api/license/registration-status"):
        assert (await team.get(path, user=STAFF_VIEWER)).status_code == 403, path        # and not platform staff either
    assert (await team.get("/api/license/status", user=LEGACY)).status_code != 403


async def test_roles_are_enforced_even_while_multi_tenancy_is_off(team, monkeypatch):
    monkeypatch.setenv("MULTI_TENANT_ENABLED", "false")
    assert (await team.post("/api/agents/", user=STAFF_VIEWER, json={"name": "x", "description": "d"})).status_code == 403
    assert (await team.post("/api/agents/", user=LEGACY, json={"name": "y", "description": "d"})).status_code in (200, 201)


async def test_me_reports_what_the_caller_may_do(team):
    caps = lambda r: r.json()["capabilities"]
    viewer = caps(await team.get("/api/me", user=VIEWER))
    operator = caps(await team.get("/api/me", user=OPERATOR))
    boss = caps(await team.get("/api/me", user=ACME_BOSS))
    assert not viewer["operate_calls"] and not viewer["manage_agents_and_keys"]
    assert operator["operate_calls"] and not operator["manage_agents_and_keys"]
    assert boss["operate_calls"] and boss["manage_agents_and_keys"]


async def test_me_says_who_is_platform_staff(team):
    me = lambda r: r.json()
    assert me(await team.get("/api/me", user=ADMIN))["is_platform_staff"] is True                   # a super admin
    legacy = me(await team.get("/api/me", user=LEGACY))
    assert legacy["is_platform_staff"] is True and legacy["capabilities"]["manage_agents_and_keys"] is True   # STL staff: full workspace
    assert me(await team.get("/api/me", user=ACME_BOSS))["is_platform_staff"] is False             # a client's administrator
    assert me(await team.get("/api/me", user=VIEWER))["is_platform_staff"] is False
    staff_domain_member = me(await team.get("/api/me", user=STAFF_VIEWER))                          # invited as a viewer: a tenant user
    assert staff_domain_member["is_platform_staff"] is False and not staff_domain_member["capabilities"]["manage_agents_and_keys"]
