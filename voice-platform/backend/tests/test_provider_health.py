import dataclasses
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.workflow_api as workflow_api
from app import provider_health
from app.config import get_config
from app.provider_catalog import build_options, default_model
from app.workflow_runtime import apply_settings, sanitize_settings, node_setting_warnings
from app.workflow_api import NodeSchema


def make_cfg(groq="g", openai="o", gemini="m", provider="groq"):
    base = get_config()
    llm = dataclasses.replace(base.llm, groq_api_key=groq, openai_api_key=openai, gemini_api_key=gemini,
                              provider=provider, openai_model="gpt-4o-mini", gemini_model="gemini-3.6-flash")
    return dataclasses.replace(base, llm=llm)


@pytest.fixture(autouse=True)
def clean_cache():
    provider_health.reset_cache()
    yield
    provider_health.reset_cache()


def resp(status, text=""):
    r = MagicMock()
    r.status_code = status
    r.text = text
    return r


def fake_post(by_host):
    async def post(self, url, **kw):
        for host, r in by_host.items():
            if host in url:
                if isinstance(r, Exception):
                    raise r
                return r
        raise AssertionError(f"unexpected url {url}")
    return post


# ─── status mapping ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status,body,expected", [
    (429, "", "quota or billing limit reached"),
    (200 + 200, "Your billing account has exceeded its monthly spending cap", "quota or billing limit reached"),
    (403, "no access to model", "this key has no access to the model"),
    (404, "model not found", "the model is not available"),
    (401, "bad", "the API key was rejected"),
    (500, "boom", "HTTP 500"),
])
def test_issue_text(status, body, expected):
    assert provider_health._issue(status, body) == expected


# ─── probing & caching ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_probe_results_and_cache():
    cfg = make_cfg()
    hosts = {"groq.com": resp(200), "api.openai.com": resp(403, "Project does not have access to model"),
             "generativelanguage": resp(429, "monthly spending cap")}
    with patch.object(httpx.AsyncClient, "post", fake_post(hosts)):
        h = await provider_health.llm_health(cfg)
    assert h["groq"]["ok"] is True and h["groq"]["issue"] is None
    assert h["openai"]["ok"] is False and "no access" in h["openai"]["issue"]
    assert h["gemini"]["ok"] is False and "billing" in h["gemini"]["issue"]
    assert provider_health.known_unhealthy("gemini") and provider_health.known_unhealthy("groq") is None

    calls = AsyncMock()
    async def counting(self, url, **kw):
        calls()
        return resp(200)
    with patch.object(httpx.AsyncClient, "post", counting):
        await provider_health.llm_health(cfg)                 # fresh cache -> no requests
        assert calls.call_count == 0
        await provider_health.llm_health(cfg, force=True)      # forced -> probes all three
        assert calls.call_count == 3
    assert provider_health.known_unhealthy("gemini") is None


@pytest.mark.asyncio
async def test_unconfigured_provider_is_not_probed_and_timeouts_are_reported():
    cfg = make_cfg(openai="", gemini="")
    with patch.object(httpx.AsyncClient, "post", fake_post({"groq.com": httpx.ReadTimeout("slow")})):
        h = await provider_health.llm_health(cfg)
    assert set(h) == {"groq"} and "did not respond" in h["groq"]["issue"]


@pytest.mark.asyncio
async def test_probes_use_the_models_the_catalog_would_offer():
    cfg = make_cfg()
    seen = {}
    async def post(self, url, **kw):
        seen[url] = (kw.get("json") or {}).get("model")
        return resp(200)
    with patch.object(httpx.AsyncClient, "post", post):
        await provider_health.llm_health(cfg)
    assert seen["https://api.openai.com/v1/chat/completions"] == "gpt-4o"
    assert any("gemini-3.6-flash:generateContent" in u for u in seen)


# ─── catalog defaults ───────────────────────────────────────────────────────────

def test_catalog_defaults_are_models_that_work():
    cfg = make_cfg()
    opts = {p["id"]: p for p in build_options(cfg)["llm"]}
    assert opts["openai"]["default_model"] == "gpt-4o" and "gpt-4o-mini" in opts["openai"]["models"]
    assert "gemini-2.5-flash" not in opts["gemini"]["models"]
    assert default_model(cfg, "groq") == cfg.llm.groq_model


# ─── runtime behaviour ──────────────────────────────────────────────────────────

def test_switching_provider_without_a_model_uses_the_catalog_default():
    cfg = make_cfg(provider="groq")
    out = apply_settings(cfg, {"llm": {"provider": "openai"}})
    assert out.llm.provider == "openai" and out.llm.openai_model == "gpt-4o"      # not the broken env default
    same = apply_settings(make_cfg(provider="openai"), {"llm": {"provider": "openai"}})
    assert same.llm.openai_model == "gpt-4o-mini"                                  # unchanged when it is already the default


def test_known_unhealthy_provider_is_ignored_unless_it_is_already_the_default():
    cfg = make_cfg(provider="groq")
    provider_health._cache["gemini"] = {"ok": False, "issue": "quota or billing limit reached", "t": 9e12}
    assert apply_settings(cfg, {"llm": {"provider": "gemini"}}).llm.provider == "groq"
    provider_health._cache["groq"] = {"ok": False, "issue": "HTTP 500", "t": 9e12}
    assert apply_settings(cfg, {"llm": {"provider": "groq"}}).llm.provider == "groq"   # never blocks the default itself


def test_warnings_mention_a_failing_provider():
    cfg = make_cfg()
    provider_health._cache["gemini"] = {"ok": False, "issue": "quota or billing limit reached", "t": 9e12}
    _, warns = sanitize_settings(cfg, {"llm": {"provider": "gemini"}})
    assert any("not working right now" in w and "billing" in w for w in warns)
    nodes = [NodeSchema(id="a", type="agentNode", position={}, data={"name": "Bot", "llm": "gemini"})]
    assert any("not working right now" in w for w in node_setting_warnings(cfg, nodes, None))


def test_node_llm_skips_a_failing_provider():
    from app.agent_router import AgentRouter
    r = AgentRouter(llm="DEFAULT", tool_executor=None, rag_engine=None, config=make_cfg())
    provider_health._cache["openai"] = {"ok": False, "issue": "this key has no access to the model", "t": 9e12}
    with patch("app.ai_services.LLMEngineFactory.get_engine", return_value="OPENAI_ENGINE") as get:
        assert r._llm_for_node({"llm": "openai"}) == "DEFAULT"
        assert get.call_count == 0


# ─── options endpoint ───────────────────────────────────────────────────────────

def test_options_endpoint_reports_health_without_network():
    fake = AsyncMock(return_value={"groq": {"ok": True, "issue": None},
                                   "gemini": {"ok": False, "issue": "quota or billing limit reached"}})
    app = FastAPI()
    app.include_router(workflow_api.router)
    with patch.object(workflow_api, "llm_health", fake):
        body = TestClient(app).get("/api/workflows/options").json()
    llm = {p["id"]: p for p in body["llm"]}
    assert llm["groq"]["healthy"] is True and llm["groq"]["issue"] is None
    assert llm["gemini"]["healthy"] is False and "billing" in llm["gemini"]["issue"]
    assert llm["openai"]["healthy"] is None                  # not probed -> unknown
