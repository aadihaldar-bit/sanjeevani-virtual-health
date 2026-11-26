import asyncio
import dataclasses
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import provider_health
from app.ai_services import OpenAILLM
from app.config import get_config
from app.interfaces import BaseLLMService
from app.llm_roles import FallbackLLM, LLMRoles


class Fake(BaseLLMService):
    supports_strict = True

    def __init__(self, result="a good answer", delay=0.0, error=None, name="fake"):
        self.result, self.delay, self.error, self.name = result, delay, error, name
        self.calls = []

    async def generate(self, prompt, history, system_prompt, tools=None, **extra):
        self.calls.append(dict(extra))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.result


def gen(llm, **kw):
    return llm.generate(prompt="hi", history=[], system_prompt="sys", tools=None, **kw)


@pytest.fixture(autouse=True)
def clean():
    provider_health.reset_cache()
    yield
    provider_health.reset_cache()


# ─── FallbackLLM: sequential ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_primary_used_when_healthy_and_asked_to_fail_loudly():
    p, s = Fake("from primary"), Fake("from fallback")
    out = await gen(FallbackLLM(p, s))
    assert out == "from primary" and s.calls == [] and p.calls[0].get("strict") is True


@pytest.mark.asyncio
@pytest.mark.parametrize("primary", [Fake(error=RuntimeError("429 rate limit")), Fake(result=""), Fake(result="  ")])
async def test_error_or_empty_answer_goes_to_fallback(primary):
    s = Fake("from fallback")
    llm = FallbackLLM(primary, s)
    assert await gen(llm) == "from fallback"
    assert llm.stats["fallback"] == 1 and s.calls[0].get("strict") is None      # last resort keeps its safe reply


@pytest.mark.asyncio
async def test_tool_call_dicts_count_as_valid():
    call = {"tool_call": True, "name": "find_doctor", "args": {}}
    assert await gen(FallbackLLM(Fake(call), Fake("x"))) == call


@pytest.mark.asyncio
async def test_without_a_fallback_behaviour_is_unchanged():
    p = Fake("plain")
    assert await gen(FallbackLLM(p, None)) == "plain"
    assert "strict" not in p.calls[0]                                             # legacy (non-strict) path


# ─── FallbackLLM: hedged ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_hedge_fast_primary_wins_without_touching_fallback():
    p, s = Fake("gpt4o answer", delay=0.01), Fake("groq answer")
    assert await gen(FallbackLLM(p, s, hedge_s=0.2)) == "gpt4o answer" and s.calls == []


@pytest.mark.asyncio
async def test_hedge_slow_primary_loses_the_race_and_latency_is_bounded():
    p, s = Fake("gpt4o answer", delay=1.0), Fake("groq answer", delay=0.05)
    t = time.perf_counter()
    out = await gen(FallbackLLM(p, s, hedge_s=0.1))
    assert out == "groq answer" and time.perf_counter() - t < 0.5                # ~hedge + fallback, not 1 s


@pytest.mark.asyncio
async def test_hedge_primary_that_finishes_just_after_the_hedge_still_wins_if_first():
    p, s = Fake("gpt4o answer", delay=0.15), Fake("groq answer", delay=0.5)
    assert await gen(FallbackLLM(p, s, hedge_s=0.1)) == "gpt4o answer"


@pytest.mark.asyncio
async def test_hedge_primary_error_uses_fallback_immediately():
    p, s = Fake(error=RuntimeError("boom")), Fake("groq answer")
    assert await gen(FallbackLLM(p, s, hedge_s=0.5)) == "groq answer"


@pytest.mark.asyncio
async def test_hedge_both_failing_returns_the_engines_safe_reply():
    p = Fake(error=RuntimeError("boom"))
    s = MagicMock()
    s.supports_strict = True
    s.generate = AsyncMock(side_effect=[RuntimeError("also down"), "safe canned reply"])
    assert await gen(FallbackLLM(p, s, hedge_s=0.05)) == "safe canned reply"
    assert s.generate.await_args_list[1].kwargs.get("strict") is None


# ─── roles ──────────────────────────────────────────────────────────────────────

def cfg(groq="g", openai="o"):
    base = get_config()
    return dataclasses.replace(base, llm=dataclasses.replace(
        base.llm, groq_api_key=groq, openai_api_key=openai, gemini_api_key="", provider="groq"))


def default_engine():
    return OpenAILLM(base_url="https://api.groq.com/openai/v1", api_key="g", model_name="openai/gpt-oss-20b")


def test_roles_assign_models_to_jobs(monkeypatch):
    monkeypatch.setenv("LLM_RAG_USE_OPENAI", "true")
    roles = LLMRoles(cfg(), default_engine())
    assert isinstance(roles.routing, FallbackLLM) and roles.routing.primary.model_name == "qwen/qwen3.8-27b"
    assert roles.routing.secondary.model_name == "gpt-4o"
    assert roles.classify is roles.routing
    assert roles.background.primary.model_name == "openai/gpt-oss-120b"

    conv = roles.conversation_for(roles.default)
    assert isinstance(conv, FallbackLLM) and conv.primary is roles.default and conv.secondary.model_name == "gpt-4o"
    assert roles.conversation_for(roles.default) is conv                       # cached

    rag = roles.rag_for(roles.default)
    assert rag.primary.model_name == "gpt-4o" and rag.secondary is roles.default and rag.hedge_s == pytest.approx(1.3)


def test_rag_on_gpt4o_is_off_unless_enabled():
    roles = LLMRoles(cfg(), default_engine())
    # latency-preserving default: grounded answers use the agent's own (fast) engine, still with the fallback behind it
    rag = roles.rag_for(roles.default)
    assert isinstance(rag, FallbackLLM) and rag.primary is roles.default and rag.hedge_s is None


def test_roles_degrade_without_keys_or_when_a_provider_is_failing(monkeypatch):
    monkeypatch.setenv("LLM_RAG_USE_OPENAI", "true")
    no_openai = LLMRoles(cfg(openai=""), default_engine())
    assert no_openai.fallback is None
    assert no_openai.conversation_for(no_openai.default) is no_openai.default
    assert no_openai.rag_for(no_openai.default) is no_openai.default
    assert no_openai.routing.primary.model_name == "qwen/qwen3.8-27b" and no_openai.routing.secondary is no_openai.default

    no_groq = LLMRoles(cfg(groq=""), default_engine())
    assert no_groq.routing.model_name == "gpt-4o"                               # only OpenAI left, used directly

    provider_health._cache["openai"] = {"ok": False, "issue": "no access", "t": 9e12}
    broken = LLMRoles(cfg(), default_engine())
    assert broken.fallback is None and broken.rag_for(broken.default) is broken.default


def test_roles_leave_stand_in_engines_alone(monkeypatch):
    stub = MagicMock()
    roles = LLMRoles(cfg(), stub)
    assert roles.conversation_for(stub) is stub and roles.rag_for(stub) is stub and roles.routing is stub

    monkeypatch.setenv("LLM_FALLBACK_ENABLED", "false")
    off = LLMRoles(cfg(), default_engine())
    assert off.conversation_for(off.default) is off.default and off.rag_for(off.default) is off.default


# ─── OpenAILLM fixes ────────────────────────────────────────────────────────────

def test_groq_engine_honours_the_requested_model():
    e = OpenAILLM(base_url="https://api.groq.com/openai/v1", api_key="g", model_name="qwen/qwen3.8-27b")
    assert e.model_name == "qwen/qwen3.8-27b"                                  # used to be forced to gpt-oss-20b
    assert e._groq_reasoning_effort("qwen/qwen3.8-27b") == "none"
    assert e._groq_reasoning_effort("openai/gpt-oss-120b") == e.settings.reasoning_effort
    assert e._groq_reasoning_effort("llama-whatever") is None


@pytest.mark.asyncio
async def test_strict_mode_raises_instead_of_inventing_an_answer():
    e = OpenAILLM(base_url="https://api.groq.com/openai/v1", api_key="g", model_name="openai/gpt-oss-20b")
    err = RuntimeError("Error code: 429 - rate limit reached")
    with patch.object(e.client.chat.completions, "create", AsyncMock(side_effect=err)):
        with pytest.raises(RuntimeError):
            await e.generate("hi", [], "sys", strict=True)
        canned = await e.generate("hi", [], "sys")                              # legacy behaviour kept
        assert isinstance(canned, str) and len(canned) > 10

    empty = MagicMock()
    empty.choices = [MagicMock(message=MagicMock(content="", tool_calls=None, reasoning_content=None))]
    empty.usage = None
    with patch.object(e.client.chat.completions, "create", AsyncMock(return_value=empty)):
        with pytest.raises(RuntimeError):
            await e.generate("hi", [], "sys", strict=True)


# ─── name extraction ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("llm_reply,expected", [("Sonika Kaldas", "Sonika Kaldas"), ('"Adi Haldar"\nextra', "Adi Haldar"),
                                                ("UNKNOWN", None), ("x" * 60, None)])
async def test_name_extraction_uses_the_background_llm(llm_reply, expected):
    from app import sarvam_name_corrector as snc
    fake = MagicMock()
    fake.generate = AsyncMock(return_value=llm_reply)
    with patch("app.llm_roles.LLMRoles.background", new_callable=lambda: property(lambda self: fake)):
        assert await snc._extract_name_with_llm("Caller: my name is Sonika Kaldas", "Sonic") == expected


@pytest.mark.asyncio
async def test_name_extraction_falls_back_to_regex_when_the_llm_fails_or_is_missing():
    from app import sarvam_name_corrector as snc
    boom = MagicMock()
    boom.generate = AsyncMock(side_effect=RuntimeError("down"))
    with patch("app.llm_roles.LLMRoles.background", new_callable=lambda: property(lambda self: boom)), \
         patch.object(snc, "_regex_extract_name", return_value="Regex Name"):
        assert await snc._extract_name_with_llm("t", "x") == "Regex Name"
    with patch("app.llm_roles.LLMRoles.background", new_callable=lambda: property(lambda self: None)), \
         patch.object(snc, "_regex_extract_name", return_value="Regex Name"):
        assert await snc._extract_name_with_llm("t", "x") == "Regex Name"


# ─── raw mode: routing JSON must survive the speech cleaner ─────────────────────

def _reply(text):
    r = MagicMock()
    r.choices = [MagicMock(message=MagicMock(content=text, tool_calls=None, reasoning_content=None))]
    r.usage = None
    return r


@pytest.mark.asyncio
async def test_speech_cleaning_breaks_json_ids_but_raw_mode_keeps_them():
    e = OpenAILLM(base_url="https://api.groq.com/openai/v1", api_key="g", model_name="qwen/qwen3.8-27b")
    payload = '{"target_node_id": "node_booking"}'
    with patch.object(e.client.chat.completions, "create", AsyncMock(return_value=_reply(payload))):
        cleaned = await e.generate("x", [], "sys")
        raw = await e.generate("x", [], "sys", raw=True)
    assert "target_node_id" not in cleaned and "targetnodeid" in cleaned          # why routing was silently failing
    assert raw == payload


@pytest.mark.asyncio
async def test_fallback_only_passes_raw_to_engines_that_support_it():
    class NoRaw(Fake):
        supports_raw = False

        async def generate(self, prompt, history, system_prompt, tools=None, **extra):
            assert "raw" not in extra
            return await super().generate(prompt, history, system_prompt, tools, **extra)

    assert await gen(FallbackLLM(NoRaw("ok answer"), Fake("fb")), raw=True) == "ok answer"
    sup = Fake("raw answer")
    sup.supports_raw = True
    assert await gen(FallbackLLM(sup, Fake("fb")), raw=True) == "raw answer" and sup.calls[0].get("raw") is True
