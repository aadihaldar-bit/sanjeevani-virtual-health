import json

import httpx
import pytest
from sqlalchemy import select

from org_env import ADMIN, env  # noqa: F401
from app import external_models as em
from app import secret_store
from app.config import get_config
from app.models import AdminAuditEntry, ModelEndpoint, ProviderCredential, Workflow

pytestmark = pytest.mark.asyncio

KEY = "gsk_live_TOPSECRETVALUE_9876"
PUBLIC = "https://93.184.216.34/v1"         # a public literal address: passes the safety check without any DNS


async def two_orgs(env):
    acme = await env.make_org("Acme", admin_email="boss@acme.test")
    globex = await env.make_org("Globex", admin_email="boss@globex.test")
    return acme, globex


# --- provider keys -----------------------------------------------------------------------------

async def test_a_stored_key_is_encrypted_hinted_and_never_returned(env):
    acme, _ = await two_orgs(env)
    put = await env.put("/api/external/credentials", user="boss@acme.test", json={"kind": "llm", "provider": "groq", "api_key": KEY})
    assert put.status_code == 200
    assert KEY not in put.text and put.json()["key_hint"] == "...9876"
    listing = await env.get("/api/external/credentials", user="boss@acme.test")
    assert KEY not in listing.text and listing.json()["credentials"][0]["provider"] == "groq"
    async with env.maker() as db:
        row = (await db.execute(select(ProviderCredential))).scalars().first()
        assert row.secret_enc != KEY and "TOPSECRET" not in row.secret_enc
        assert secret_store.decrypt(row.secret_enc) == KEY
        audit_text = json.dumps([e.to_dict() for e in (await db.execute(select(AdminAuditEntry))).scalars().all()])
        assert "TOPSECRET" not in audit_text and "credential.set" in audit_text            # logged, but not the value


async def test_replacing_a_key_updates_the_same_row(env):
    await two_orgs(env)
    for key in (KEY, "gsk_second_key_value_5555"):
        await env.put("/api/external/credentials", user="boss@acme.test", json={"kind": "llm", "provider": "groq", "api_key": key})
    creds = (await env.get("/api/external/credentials", user="boss@acme.test")).json()["credentials"]
    assert len(creds) == 1 and creds[0]["key_hint"] == "...5555"


async def test_organizations_cannot_see_or_delete_each_others_keys(env):
    await two_orgs(env)
    await env.put("/api/external/credentials", user="boss@acme.test", json={"kind": "tts", "provider": "cartesia", "api_key": KEY})
    assert (await env.get("/api/external/credentials", user="boss@globex.test")).json()["credentials"] == []
    cred_id = (await env.get("/api/external/credentials", user="boss@acme.test")).json()["credentials"][0]["id"]
    assert (await env.delete(f"/api/external/credentials/{cred_id}", user="boss@globex.test")).status_code == 403
    assert (await env.get("/api/external/credentials?tenant_id=1", user="boss@globex.test")).status_code in (200, 403)
    assert (await env.delete(f"/api/external/credentials/{cred_id}", user="boss@acme.test")).status_code == 200


async def test_members_cannot_manage_keys_and_input_is_validated(env):
    acme, _ = await two_orgs(env)
    await env.post(f"/api/tenants/{acme['id']}/members", json={"email": "sam@acme.test", "role": "operator"})
    assert (await env.put("/api/external/credentials", user="sam@acme.test", json={"kind": "llm", "provider": "groq", "api_key": KEY})).status_code == 403
    bad_provider = await env.put("/api/external/credentials", user="boss@acme.test", json={"kind": "llm", "provider": "skynet", "api_key": KEY})
    short = await env.put("/api/external/credentials", user="boss@acme.test", json={"kind": "llm", "provider": "groq", "api_key": "abc"})
    spaces = await env.put("/api/external/credentials", user="boss@acme.test", json={"kind": "llm", "provider": "groq", "api_key": "has some spaces in it"})
    assert (bad_provider.status_code, short.status_code, spaces.status_code) == (400, 400, 400)


async def test_without_an_encryption_key_nothing_is_stored(env, monkeypatch):
    await two_orgs(env)
    monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEY")
    response = await env.put("/api/external/credentials", user="boss@acme.test", json={"kind": "llm", "provider": "groq", "api_key": KEY})
    assert response.status_code == 503 and "CREDENTIAL_ENCRYPTION_KEY" in response.json()["detail"]
    assert (await env.get("/api/external/status")).json()["encryption_configured"] is False
    async with env.maker() as db:
        assert (await db.execute(select(ProviderCredential))).first() is None


async def test_platform_admin_must_name_the_organization(env):
    acme, _ = await two_orgs(env)
    assert (await env.put("/api/external/credentials", json={"kind": "llm", "provider": "groq", "api_key": KEY})).status_code == 400
    ok = await env.put("/api/external/credentials", json={"tenant_id": acme["id"], "kind": "llm", "provider": "groq", "api_key": KEY})
    assert ok.status_code == 200


async def test_the_organizations_key_replaces_the_platform_key_for_its_calls_only(env):
    acme, _ = await two_orgs(env)
    await env.put("/api/external/credentials", user="boss@acme.test", json={"kind": "llm", "provider": "groq", "api_key": KEY})
    await env.put("/api/external/credentials", user="boss@acme.test", json={"kind": "tts", "provider": "cartesia", "api_key": "cartesia-key-123456"})
    base = get_config()
    async with env.maker() as db:
        creds = await em.load_org_credentials(db, acme["id"])
        none = await em.load_org_credentials(db, None)
    swapped = em.apply_org_credentials(base, creds)
    assert swapped.llm.groq_api_key == KEY and swapped.tts.cartesia_api_key == "cartesia-key-123456"
    assert swapped.llm.openai_api_key == base.llm.openai_api_key and swapped.stt == base.stt           # nothing else changes
    assert base.llm.groq_api_key != KEY                                                                  # the platform config is untouched
    assert em.apply_org_credentials(base, none) is base


# --- custom endpoints --------------------------------------------------------------------------------

def endpoint_body(**over):
    body = {"name": "Acme vLLM", "base_url": PUBLIC, "model": "llama-3.1-70b", "api_key": "endpoint-secret-0001",
            "supports_tools": True, "acknowledge_data_sharing": True}
    body.update(over)
    return body


async def test_an_endpoint_needs_the_data_sharing_acknowledgement(env):
    await two_orgs(env)
    response = await env.post("/api/external/endpoints", user="boss@acme.test", json=endpoint_body(acknowledge_data_sharing=False))
    assert response.status_code == 400 and "transcripts" in response.json()["detail"]


@pytest.mark.parametrize("url", ["http://93.184.216.34/v1", "https://10.0.0.5/v1", "https://127.0.0.1:8000/v1",
                                 "https://169.254.169.254/latest", "https://user:pw@93.184.216.34/v1", "https://localhost/v1"])
async def test_unsafe_endpoint_urls_are_refused(env, url):
    await two_orgs(env)
    response = await env.post("/api/external/endpoints", user="boss@acme.test", json=endpoint_body(base_url=url))
    assert response.status_code == 400, response.text


async def test_endpoint_secrets_are_write_only(env):
    await two_orgs(env)
    created = await env.post("/api/external/endpoints", user="boss@acme.test",
                             json=endpoint_body(headers={"X-Api-Version": "2026-01"}))
    assert created.status_code == 201
    assert "endpoint-secret-0001" not in created.text and "2026-01" not in created.text
    body = created.json()
    assert body["has_api_key"] and body["has_headers"] and body["provider_id"] == f"custom:{body['id']}"
    async with env.maker() as db:
        row = await db.get(ModelEndpoint, body["id"])
        assert "endpoint-secret" not in row.api_key_enc and "2026" not in row.headers_enc
    listing = await env.get("/api/external/endpoints", user="boss@acme.test")
    assert "endpoint-secret-0001" not in listing.text


async def test_updating_without_a_key_keeps_the_stored_key(env):
    await two_orgs(env)
    eid = (await env.post("/api/external/endpoints", user="boss@acme.test", json=endpoint_body())).json()["id"]
    changed = await env.put(f"/api/external/endpoints/{eid}", user="boss@acme.test",
                            json=endpoint_body(model="llama-3.3-70b", api_key=None))
    assert changed.status_code == 200 and changed.json()["model"] == "llama-3.3-70b" and changed.json()["has_api_key"]
    async with env.maker() as db:
        resolved = await em.resolve_endpoint(db, eid, (await db.get(ModelEndpoint, eid)).tenant_id)
    assert resolved["api_key"] == "endpoint-secret-0001" and resolved["model"] == "llama-3.3-70b"


async def test_forbidden_or_malformed_headers_are_refused(env):
    await two_orgs(env)
    for headers in ({"Host": "evil.example.com"}, {"Content-Length": "5"}, {"X-A\r\nInjected": "1"}, {f"X-H{i}": "v" for i in range(11)}):
        response = await env.post("/api/external/endpoints", user="boss@acme.test", json=endpoint_body(headers=headers))
        assert response.status_code == 400, headers


async def test_endpoints_belong_to_an_organization_and_platform_wide_ones_are_admin_only(env):
    await two_orgs(env)
    mine = (await env.post("/api/external/endpoints", user="boss@acme.test", json=endpoint_body())).json()
    shared = (await env.post("/api/external/endpoints", json=endpoint_body(name="Shared", platform_wide=True))).json()
    assert shared["tenant_id"] is None
    assert (await env.post("/api/external/endpoints", user="boss@acme.test", json=endpoint_body(platform_wide=True))).status_code == 403
    globex_sees = {e["name"] for e in (await env.get("/api/external/endpoints", user="boss@globex.test")).json()["endpoints"]}
    assert globex_sees == {"Shared"}                                         # not Acme's private endpoint
    assert (await env.put(f"/api/external/endpoints/{mine['id']}", user="boss@globex.test", json=endpoint_body())).status_code == 403
    assert (await env.delete(f"/api/external/endpoints/{shared['id']}", user="boss@acme.test")).status_code == 403
    assert (await env.delete(f"/api/external/endpoints/{mine['id']}", user="boss@acme.test")).status_code == 200


async def test_endpoint_test_records_success_with_latency(env):
    acme, _ = await two_orgs(env)
    eid = (await env.post("/api/external/endpoints", user="boss@acme.test", json=endpoint_body())).json()["id"]
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"], seen["url"], seen["body"] = request.headers.get("authorization"), str(request.url), json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    async with env.maker() as db:
        row = await db.get(ModelEndpoint, eid)
        result = await em.test_endpoint(db, row, transport=httpx.MockTransport(handler))
        await db.commit()
    assert result["ok"] and result["ms"] >= 0
    assert seen["auth"] == "Bearer endpoint-secret-0001" and seen["url"].endswith("/v1/chat/completions")
    assert seen["body"]["model"] == "llama-3.1-70b" and seen["body"]["max_tokens"] == 5
    listed = (await env.get("/api/external/endpoints", user="boss@acme.test")).json()["endpoints"][0]
    assert listed["last_test_ok"] is True and listed["last_test_error"] is None


async def test_a_failing_endpoint_reports_the_problem_without_leaking_the_key(env):
    await two_orgs(env)
    eid = (await env.post("/api/external/endpoints", user="boss@acme.test", json=endpoint_body())).json()["id"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="Invalid key endpoint-secret-0001 for this model")

    async with env.maker() as db:
        result = await em.test_endpoint(db, await db.get(ModelEndpoint, eid), transport=httpx.MockTransport(handler))
        await db.commit()
    assert not result["ok"] and "401" in result["error"] and "endpoint-secret-0001" not in result["error"] and "***" in result["error"]


async def test_a_connection_error_is_reported_not_raised(env):
    await two_orgs(env)
    eid = (await env.post("/api/external/endpoints", user="boss@acme.test", json=endpoint_body())).json()["id"]

    def handler(request):
        raise httpx.ConnectTimeout("timed out")

    async with env.maker() as db:
        result = await em.test_endpoint(db, await db.get(ModelEndpoint, eid), transport=httpx.MockTransport(handler))
    assert result["ok"] is False and "ConnectTimeout" in result["error"]


async def test_an_endpoint_is_usable_only_by_its_own_organization_and_only_while_enabled(env):
    acme, globex = await two_orgs(env)
    eid = (await env.post("/api/external/endpoints", user="boss@acme.test", json=endpoint_body())).json()["id"]
    async with env.maker() as db:
        assert (await em.resolve_endpoint(db, eid, acme["id"]))["base_url"] == PUBLIC
        assert await em.resolve_endpoint(db, eid, globex["id"]) is None
        assert await em.resolve_endpoint(db, eid, None) is None
        assert await em.resolve_endpoint(db, 9999, acme["id"]) is None
    await env.put(f"/api/external/endpoints/{eid}", user="boss@acme.test", json=endpoint_body(enabled=False, api_key=None))
    async with env.maker() as db:
        assert await em.resolve_endpoint(db, eid, acme["id"]) is None


# --- what a call actually uses ---------------------------------------------------------------------------

@pytest.fixture
def quiet_health(monkeypatch):
    import app.provider_health as ph
    monkeypatch.setattr(ph, "refresh_in_background", lambda cfg: None)


async def make_workflow(env, tenant_id, llm):
    async with env.maker() as db:
        wf = Workflow(name="Desk", tenant_id=tenant_id, settings_json={"llm": llm})
        db.add(wf)
        await db.commit()
        return wf.id


async def test_a_workflow_can_run_on_the_organizations_own_endpoint(env, quiet_health):
    from app.ai_services import LLMEngineFactory
    from app.workflow_runtime import effective_config
    acme, _ = await two_orgs(env)
    eid = (await env.post("/api/external/endpoints", user="boss@acme.test",
                          json=endpoint_body(headers={"X-Tenant": "acme"}))).json()["id"]
    wf_id = await make_workflow(env, acme["id"], {"provider": f"custom:{eid}"})
    cfg = await effective_config(wf_id)
    assert cfg.llm.provider == "custom" and cfg.llm.custom_base_url == PUBLIC and cfg.llm.custom_model == "llama-3.1-70b"
    assert cfg.llm.custom_api_key == "endpoint-secret-0001" and dict(cfg.llm.custom_headers) == {"X-Tenant": "acme"}
    engine = LLMEngineFactory.get_engine(cfg)
    assert engine.model_name == "llama-3.1-70b" and str(engine.client.base_url).startswith("https://93.184.216.34/v1")
    assert engine.client.default_headers.get("X-Tenant") == "acme"
    assert "endpoint-secret" not in repr(cfg)                                    # secrets stay out of printed config


async def test_another_organizations_endpoint_is_ignored_and_the_default_model_is_used(env, quiet_health):
    from app.workflow_runtime import effective_config
    acme, globex = await two_orgs(env)
    eid = (await env.post("/api/external/endpoints", user="boss@acme.test", json=endpoint_body())).json()["id"]
    wf_id = await make_workflow(env, globex["id"], {"provider": f"custom:{eid}"})
    cfg = await effective_config(wf_id)
    assert cfg.llm.provider != "custom" and cfg.llm.custom_base_url == ""


async def test_a_workflow_uses_its_organizations_provider_keys(env, quiet_health):
    from app.workflow_runtime import effective_config
    acme, globex = await two_orgs(env)
    await env.put("/api/external/credentials", user="boss@acme.test", json={"kind": "llm", "provider": "groq", "api_key": KEY})
    mine = await make_workflow(env, acme["id"], {"provider": "groq"})
    theirs = await make_workflow(env, globex["id"], {"provider": "groq"})
    assert (await effective_config(mine)).llm.groq_api_key == KEY
    assert (await effective_config(theirs)).llm.groq_api_key != KEY


async def test_saving_settings_accepts_a_visible_custom_provider_and_drops_others():
    from app.workflow_runtime import sanitize_settings
    extra = [{"id": "custom:7", "label": "Acme (custom)", "available": True, "models": ["m"]}]
    clean, warnings = sanitize_settings(get_config(), {"llm": {"provider": "custom:7", "model": "m"}}, extra_llm=extra)
    assert clean["llm"]["provider"] == "custom:7" and not warnings
    clean, warnings = sanitize_settings(get_config(), {"llm": {"provider": "custom:8"}}, extra_llm=extra)
    assert "provider" not in clean.get("llm", {}) and warnings


# --- a client's own OpenAI key (the platform has none) -----------------------------------------------------

OPENAI_KEY = "sk-proj-CLIENTOWNKEY_000111222"


@pytest.fixture
def platform_without_openai(monkeypatch, quiet_health):
    import dataclasses
    import app.config as config_module
    import app.provider_health as ph
    base = get_config()
    cfg = dataclasses.replace(base, llm=dataclasses.replace(base.llm, openai_api_key=""))
    monkeypatch.setattr(config_module, "_config_instance", cfg)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    ph.reset_cache()
    yield cfg
    ph.reset_cache()


async def test_a_clients_own_openai_key_runs_their_calls_even_when_the_platform_has_none(env, platform_without_openai):
    from app.ai_services import LLMEngineFactory
    from app.workflow_runtime import effective_config
    acme, globex = await two_orgs(env)
    assert (await env.put("/api/external/credentials", user="boss@acme.test",
                          json={"kind": "llm", "provider": "openai", "api_key": OPENAI_KEY})).status_code == 200
    mine = await make_workflow(env, acme["id"], {"provider": "openai", "model": "gpt-4o"})
    theirs = await make_workflow(env, globex["id"], {"provider": "openai", "model": "gpt-4o"})

    cfg = await effective_config(mine)
    assert cfg.llm.provider == "openai" and cfg.llm.openai_model == "gpt-4o" and cfg.llm.openai_api_key == OPENAI_KEY
    engine = LLMEngineFactory.get_engine(cfg)
    assert engine.client.api_key == OPENAI_KEY and str(engine.client.base_url).startswith("https://api.openai.com/v1")
    assert OPENAI_KEY not in repr(cfg)

    other = await effective_config(theirs)                    # another organization never gets the key
    assert other.llm.openai_api_key == "" and other.llm.provider != "openai"


async def test_the_builder_offers_openai_to_the_client_that_has_its_own_key(env, platform_without_openai):
    await two_orgs(env)
    await env.put("/api/external/credentials", user="boss@acme.test", json={"kind": "llm", "provider": "openai", "api_key": OPENAI_KEY})

    def openai(resp):
        assert resp.status_code == 200, resp.text
        return next(p for p in resp.json()["llm"] if p["id"] == "openai")

    assert openai(await env.get("/api/workflows/options", user="boss@acme.test"))["available"] is True
    assert openai(await env.get("/api/workflows/options", user="boss@globex.test"))["available"] is False


async def test_choosing_openai_for_an_agent_is_saved_without_a_not_configured_warning(env, platform_without_openai):
    acme, _ = await two_orgs(env)
    await env.put("/api/external/credentials", user="boss@acme.test", json={"kind": "llm", "provider": "openai", "api_key": OPENAI_KEY})
    wf_id = await make_workflow(env, acme["id"], {})
    node = {"id": "a1", "type": "agentNode", "position": {"x": 0, "y": 0}, "data": {"name": "Desk", "system_prompt": "Help callers."}}
    body = {"id": wf_id, "name": "Desk", "nodes": [node], "edges": [], "settings": {"llm": {"provider": "openai", "model": "gpt-4o"}}}
    res = await env.post("/api/workflows/", user="boss@acme.test", json=body)
    text = json.dumps(res.json())
    assert res.status_code == 200, text
    assert "not configured" not in text and OPENAI_KEY not in text
    async with env.maker() as db:
        assert (await db.get(Workflow, wf_id)).settings_json["llm"]["provider"] == "openai"


async def test_the_platforms_broken_key_does_not_block_a_clients_working_key(env, platform_without_openai, monkeypatch):
    """The platform's OpenAI key hit its spending cap; the client's own key must still be used."""
    import dataclasses
    import app.config as config_module
    import app.provider_health as ph
    from app.workflow_runtime import effective_config
    acme, _ = await two_orgs(env)
    platform = dataclasses.replace(platform_without_openai, llm=dataclasses.replace(platform_without_openai.llm, openai_api_key="sk-platform-capped"))
    monkeypatch.setattr(config_module, "_config_instance", platform)
    ph._cache["openai"] = {"ok": False, "issue": "quota or billing limit reached", "t": 9e12, "fp": ph._fingerprint("sk-platform-capped")}
    await env.put("/api/external/credentials", user="boss@acme.test", json={"kind": "llm", "provider": "openai", "api_key": OPENAI_KEY})
    mine = await make_workflow(env, acme["id"], {"provider": "openai", "model": "gpt-4o"})
    plain = await make_workflow(env, None, {"provider": "openai", "model": "gpt-4o"})
    assert (await effective_config(mine)).llm.openai_api_key == OPENAI_KEY            # their own key: not blocked
    assert (await effective_config(plain)).llm.provider != "openai"                   # the platform's key still is


async def test_a_saved_key_can_be_checked_and_the_answer_never_contains_it(env, platform_without_openai, monkeypatch):
    import app.external_models_api as api

    seen = {}

    async def fake_probe(cfg, provider):
        seen["key"], seen["provider"] = cfg.llm.openai_api_key, provider
        return {"ok": False, "issue": "quota or billing limit reached", "model": "gpt-4o"}

    monkeypatch.setattr(api, "probe_key", fake_probe)
    await two_orgs(env)
    await env.put("/api/external/credentials", user="boss@acme.test", json={"kind": "llm", "provider": "openai", "api_key": OPENAI_KEY})
    cred = (await env.get("/api/external/credentials", user="boss@acme.test")).json()["credentials"][0]
    res = await env.post(f"/api/external/credentials/{cred['id']}/test", user="boss@acme.test")
    assert res.status_code == 200 and res.json()["ok"] is False and "quota" in res.json()["issue"]
    assert seen == {"key": OPENAI_KEY, "provider": "openai"} and OPENAI_KEY not in res.text
    assert (await env.post(f"/api/external/credentials/{cred['id']}/test", user="boss@globex.test")).status_code == 403
