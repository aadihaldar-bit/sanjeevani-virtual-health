import dataclasses
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.future import select

from app.ai_services import DEEPGRAM_VOICE_MAP, SARVAM_SPEAKERS
from app.config import get_config
from app.provider_catalog import build_options, provider_available
from app.workflow_runtime import apply_settings, effective_config, node_setting_warnings, sanitize_settings
from app.workflow_api import NodeSchema, WorkflowSchema, EdgeSchema, save_workflow
import app.workflow_api as workflow_api


def make_cfg(**keys):
    base = get_config()
    llm = dataclasses.replace(base.llm, groq_api_key=keys.get("groq", ""), openai_api_key=keys.get("openai", ""),
                              gemini_api_key=keys.get("gemini", ""), provider="groq")
    stt = dataclasses.replace(base.stt, cartesia_api_key=keys.get("cartesia", ""), deepgram_api_key=keys.get("dg_stt", ""),
                              gnani_api_key="", sarvam_api_key="", provider="cartesia", language_code="en-IN")
    tts = dataclasses.replace(base.tts, deepgram_api_key=keys.get("dg_tts", ""), cartesia_api_key="", sarvam_api_key="",
                              gnani_api_key="", murf_api_key="", azure_speech_key="", provider="deepgram")
    return dataclasses.replace(base, llm=llm, stt=stt, tts=tts)


# ─── catalog ───────────────────────────────────────────────────────────────────

def test_availability_follows_configured_keys():
    cfg = make_cfg(groq="k", dg_tts="k")
    assert provider_available(cfg, "llm", "groq") and not provider_available(cfg, "llm", "openai")
    assert provider_available(cfg, "tts", "deepgram") and not provider_available(cfg, "tts", "murf")
    assert provider_available(cfg, "stt", "groq")            # Whisper rides on the Groq key
    assert not provider_available(cfg, "llm", "nonsense")


def test_options_shape_and_voices_match_engine_maps():
    opts = build_options(make_cfg(groq="k", dg_tts="k"))
    assert [p["id"] for p in opts["llm"]] == ["groq", "openai", "gemini"]
    assert all(p["models"] and p["default_model"] == p["models"][0] for p in opts["llm"])
    tts = {p["id"]: p for p in opts["tts"]}
    assert {v["id"].lower() for v in tts["deepgram"]["voices"]} == set(DEEPGRAM_VOICE_MAP)
    assert {v["id"] for v in tts["sarvam"]["voices"]} == set(SARVAM_SPEAKERS)
    assert tts["deepgram"]["available"] is True and tts["azure"]["available"] is False
    assert {t["id"] for t in opts["tools"]} == {"transfer_to_human", "create_symphony_ticket"}
    assert opts["defaults"]["tts"]["provider"] == "deepgram"


def test_options_route_is_not_shadowed_by_workflow_id_route():
    app = FastAPI()
    app.include_router(workflow_api.router)
    with patch.object(workflow_api, "llm_health", AsyncMock(return_value={})):      # no network in tests
        r = TestClient(app).get("/api/workflows/options")
    assert r.status_code == 200 and "tts" in r.json()


# ─── applying settings ─────────────────────────────────────────────────────────

def test_apply_settings_changes_provider_model_language():
    cfg = make_cfg(groq="g", gemini="m", dg_stt="d", dg_tts="t")
    out = apply_settings(cfg, {"llm": {"provider": "gemini", "model": "gemini-2.5-flash"},
                               "stt": {"provider": "deepgram", "language": "hi-IN"},
                               "tts": {"provider": "deepgram"}})
    assert out.llm.provider == "gemini" and out.llm.gemini_model == "gemini-2.5-flash"
    assert out.stt.provider == "deepgram" and out.stt.language_code == "hi-IN"
    assert cfg.llm.provider == "groq"                        # original untouched


def test_apply_settings_ignores_unavailable_provider_and_noop_returns_same_object():
    cfg = make_cfg(groq="g")
    assert apply_settings(cfg, None) is cfg
    out = apply_settings(cfg, {"llm": {"provider": "openai", "model": "gpt-4o"}, "tts": {"provider": "murf"}})
    assert out.llm.provider == "groq" and out.tts.provider == "deepgram"


def test_sanitize_settings():
    cfg = make_cfg(groq="g", dg_tts="t")
    clean, warns = sanitize_settings(cfg, {
        "llm": {"provider": "GROQ", "model": "x" * 300, "junk": 1},
        "stt": {"provider": "madeup", "language": "hi-IN"},
        "tts": {"provider": "murf"},
        "evil": {"a": 1},
    })
    assert clean["llm"]["provider"] == "groq" and len(clean["llm"]["model"]) == 100 and "junk" not in clean["llm"]
    assert clean["stt"] == {"language": "hi-IN"}
    assert clean["tts"] == {"provider": "murf"} and "evil" not in clean
    assert any("madeup" in w for w in warns) and any("not configured" in w for w in warns)
    assert sanitize_settings(cfg, "not a dict") == ({}, [])


def test_node_setting_warnings():
    cfg = make_cfg(groq="g", dg_tts="t")
    nodes = [NodeSchema(id="a", type="agentNode", position={}, data={"name": "Bot", "llm": "openai", "voice": "Nobody"}),
             NodeSchema(id="b", type="agentNode", position={}, data={"name": "Fine", "llm": "groq", "voice": "Aarohi"}),
             NodeSchema(id="t", type="toolNode", position={}, data={"voice": "Nobody"})]
    warns = node_setting_warnings(cfg, nodes, None)
    assert len(warns) == 2 and all("Bot" in w for w in warns)


# ─── DB-driven effective config & persistence ───────────────────────────────────

@pytest_asyncio.fixture
async def session(tmp_path):
    from app.models import Base
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'s.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    with patch("app.database.async_session", maker):
        async with maker() as db:
            yield db
    await engine.dispose()


def graph(prompt="hi"):
    return dict(nodes=[NodeSchema(id="s", type="startNode", position={}, data={}),
                       NodeSchema(id="a", type="agentNode", position={}, data={"name": "A", "system_prompt": prompt})],
                edges=[EdgeSchema(id="e", source="s", target="a")])


@pytest.mark.asyncio
async def test_settings_saved_returned_and_applied_to_calls(session):
    from app.models import Workflow
    cfg = make_cfg(groq="g", gemini="m", dg_tts="t")
    with patch.object(workflow_api, "get_config", return_value=cfg):
        res = await save_workflow(WorkflowSchema(name="W", is_active=True, **graph(),
                                                 settings={"llm": {"provider": "gemini"}, "stt": {"language": "ta-IN"},
                                                           "junk": {"x": 1}}), session)
    from sqlalchemy.orm import selectinload
    wf = (await session.execute(select(Workflow).where(Workflow.id == res["id"])
                                .options(selectinload(Workflow.nodes), selectinload(Workflow.edges)))).scalars().first()
    assert wf.settings_json == {"llm": {"provider": "gemini"}, "stt": {"language": "ta-IN"}}
    assert wf.to_dict()["settings"]["stt"]["language"] == "ta-IN"

    eff = await effective_config(base=cfg)
    assert eff.llm.provider == "gemini" and eff.stt.language_code == "ta-IN"


@pytest.mark.asyncio
async def test_resave_without_settings_keeps_them(session):
    from app.models import Workflow
    cfg = make_cfg(groq="g")
    with patch.object(workflow_api, "get_config", return_value=cfg):
        first = await save_workflow(WorkflowSchema(name="W", is_active=True, **graph(),
                                                   settings={"stt": {"language": "hi-IN"}}), session)
        await save_workflow(WorkflowSchema(id=first["id"], name="W2", is_active=True, **graph()), session)  # old client
    wf = (await session.execute(select(Workflow).where(Workflow.id == first["id"]))).scalars().first()
    assert wf.name == "W2" and wf.settings_json == {"stt": {"language": "hi-IN"}}


@pytest.mark.asyncio
async def test_effective_config_defaults_when_no_workflow_or_db_error(session):
    cfg = make_cfg(groq="g")
    assert await effective_config(base=cfg) == cfg           # no workflow at all
    with patch("app.workflow_runtime.load_workflow_settings", side_effect=RuntimeError("db down")):
        assert await effective_config(base=cfg) is cfg


# ─── per-node LLM ───────────────────────────────────────────────────────────────

def make_router(cfg):
    from app.agent_router import AgentRouter
    return AgentRouter(llm="DEFAULT_LLM", tool_executor=None, rag_engine=None, config=cfg)


def test_node_llm_default_and_missing_key_fall_back():
    r = make_router(make_cfg(groq="g"))
    assert r._llm_for_node(None) == "DEFAULT_LLM"
    assert r._llm_for_node({"llm": "groq"}) == "DEFAULT_LLM"                # same as the call default
    assert r._llm_for_node({"llm": "openai", "llm_model": "gpt-4o"}) == "DEFAULT_LLM"   # no OpenAI key


def test_node_llm_builds_requested_engine_once():
    r = make_router(make_cfg(groq="g", gemini="m"))
    with patch("app.ai_services.LLMEngineFactory.get_engine", return_value="GEMINI_ENGINE") as get:
        assert r._llm_for_node({"llm": "gemini", "llm_model": "gemini-2.5-flash"}) == "GEMINI_ENGINE"
        assert r._llm_for_node({"llm": "gemini", "llm_model": "gemini-2.5-flash"}) == "GEMINI_ENGINE"
        used = get.call_args.args[0]
    assert get.call_count == 1                                              # cached
    assert used.llm.provider == "gemini" and used.llm.gemini_model == "gemini-2.5-flash"
