"""Which LLM does which job, and what happens when one fails.

  conversation  the agent's own model (Groq gpt-oss-20b by default); on error falls back to OpenAI
  routing/classify  a small fast Groq model with its own rate-limit quota (qwen3.8-27b)
  rag           OPT-IN (LLM_RAG_USE_OPENAI=true): OpenAI gpt-4o for answers grounded in retrieved
                documents. Measured on the VM it adds ~600 ms (p50) / ~1.1 s (p90) per RAG turn versus
                Groq, and the hedge cannot hide that, so it is off by default.
  background    post-call jobs (patient-name correction): Groq gpt-oss-120b, OpenAI as fallback

Everything degrades to the old single-model behaviour when a provider has no key, is known to be
failing, or LLM_FALLBACK_ENABLED=false.
"""
import asyncio
import logging
import os
from typing import Any, Dict, Optional

from .config import AppConfig
from .interfaces import BaseLLMService
from .provider_catalog import provider_available
from .provider_health import known_unhealthy

logger = logging.getLogger(__name__)


def _valid(result: Any) -> bool:
    if result is None:
        return False
    if isinstance(result, str):
        return len(result.strip()) >= 3
    return True


def _flag(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


class FallbackLLM(BaseLLMService):
    """primary, then secondary on an error or an empty answer. With hedge_s the secondary is raced
    against a primary that is slower than hedge_s and the first good answer wins."""

    def __init__(self, primary: Any, secondary: Any = None, name: str = "llm", hedge_s: Optional[float] = None):
        self.primary = primary
        self.secondary = secondary
        self.name = name
        self.hedge_s = hedge_s
        self.stats = {"primary": 0, "fallback": 0}

    @staticmethod
    async def _call(engine: Any, strict: bool, **kw: Any) -> Any:
        if strict and getattr(engine, "supports_strict", False):
            kw = {**kw, "strict": True}
        if kw.get("raw") and not getattr(engine, "supports_raw", False):
            kw = {k: v for k, v in kw.items() if k != "raw"}
        return await engine.generate(**kw)

    async def generate(self, prompt: str, history: Any, system_prompt: str, tools: Any = None, **extra: Any) -> Any:
        kw = dict(prompt=prompt, history=history, system_prompt=system_prompt, tools=tools, **extra)
        if self.secondary is None:
            return await self._call(self.primary, False, **kw)   # nothing to fall back to: unchanged behaviour
        return await (self._hedged(kw) if self.hedge_s else self._sequential(kw))

    async def generate_stream(self, prompt: str, history: Any, system_prompt: str):
        async for chunk in self.primary.generate_stream(prompt, history, system_prompt):
            yield chunk

    async def _sequential(self, kw: Dict[str, Any]) -> Any:
        try:
            result = await self._call(self.primary, True, **kw)
            if _valid(result):
                self.stats["primary"] += 1
                return result
            reason = "empty answer"
        except Exception as exc:
            reason = str(exc)[:140]
        logger.warning(f"[LLM/{self.name}] primary failed ({reason}); using fallback")
        self.stats["fallback"] += 1
        return await self._call(self.secondary, False, **kw)

    async def _hedged(self, kw: Dict[str, Any]) -> Any:
        t1: Optional[asyncio.Future] = asyncio.ensure_future(self._call(self.primary, True, **kw))
        try:
            result = await asyncio.wait_for(asyncio.shield(t1), timeout=self.hedge_s)
            if _valid(result):
                self.stats["primary"] += 1
                return result
            t1 = None
        except asyncio.TimeoutError:
            logger.info(f"[LLM/{self.name}] primary slower than {int(self.hedge_s * 1000)} ms; racing the fallback")
        except Exception as exc:
            logger.warning(f"[LLM/{self.name}] primary failed ({str(exc)[:140]}); using fallback")
            t1 = None

        t2 = asyncio.ensure_future(self._call(self.secondary, True, **kw))
        pending = {t for t in (t1, t2) if t is not None}
        try:
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                for d in done:
                    try:
                        result = d.result()
                    except Exception as exc:
                        logger.warning(f"[LLM/{self.name}] {'primary' if d is t1 else 'fallback'} failed: {str(exc)[:140]}")
                        continue
                    if _valid(result):
                        self.stats["primary" if d is t1 else "fallback"] += 1
                        return result
        finally:
            for p in pending:
                p.cancel()
        self.stats["fallback"] += 1
        return await self._call(self.secondary, False, **kw)   # last resort: the engine's own safe reply


class LLMRoles:
    """Builds the engine for each role from the call's config; engines are created lazily and cached."""

    def __init__(self, cfg: AppConfig, default_llm: Any = None):
        self.cfg = cfg
        self.default = default_llm
        self.enabled = _flag("LLM_FALLBACK_ENABLED", True)
        # Only re-route when the router holds a real engine; stand-ins (tests, stubs) are left alone.
        self.active = default_llm is None or isinstance(default_llm, BaseLLMService)
        self.routing_model = os.getenv("LLM_ROUTING_MODEL", "qwen/qwen3.8-27b").strip()
        self.background_model = os.getenv("LLM_BACKGROUND_MODEL", "openai/gpt-oss-120b").strip()
        self.fallback_model = os.getenv("LLM_FALLBACK_MODEL", "gpt-4o").strip()
        self.rag_model = os.getenv("LLM_RAG_MODEL", "gpt-4o").strip()
        self.rag_enabled = _flag("LLM_RAG_USE_OPENAI", False)
        self.rag_hedge_s = int(os.getenv("LLM_RAG_HEDGE_MS", "1300")) / 1000.0
        self._engines: Dict[Any, Any] = {}
        self._wrapped: Dict[Any, Any] = {}

    # ── engine construction ──
    def _usable(self, provider: str) -> bool:
        return provider_available(self.cfg, "llm", provider) and not known_unhealthy(provider, self.cfg)

    def _engine(self, provider: str, model: str) -> Optional[Any]:
        key = (provider, model)
        if key not in self._engines:
            engine = None
            if self._usable(provider):
                try:
                    from .ai_services import LLMEngineFactory
                    from .workflow_runtime import apply_settings
                    engine = LLMEngineFactory.get_engine(
                        apply_settings(self.cfg, {"llm": {"provider": provider, "model": model}}))
                except Exception as exc:
                    logger.warning(f"[LLM] could not build {provider}/{model}: {exc}")
            self._engines[key] = engine
        return self._engines[key]

    @property
    def fallback(self) -> Optional[Any]:
        return self._engine("openai", self.fallback_model) if (self.enabled and self.active) else None

    def _is_fallback_engine(self, engine: Any) -> bool:
        base = str(getattr(getattr(engine, "client", None), "base_url", ""))
        return getattr(engine, "model_name", None) == self.fallback_model and "openai.com" in base

    def _wrap(self, key: Any, primary: Any, secondary: Any, name: str, hedge_s: Optional[float] = None) -> Any:
        if key not in self._wrapped:
            if primary is None:
                self._wrapped[key] = secondary or self.default
            elif secondary is None or not self.enabled:
                self._wrapped[key] = primary
            else:
                self._wrapped[key] = FallbackLLM(primary, secondary, name, hedge_s)
        return self._wrapped[key]

    # ── roles ──
    def conversation_for(self, engine: Any) -> Any:
        """The agent's own engine with the OpenAI fallback behind it."""
        if not self.active or engine is None or self._is_fallback_engine(engine):
            return engine
        return self._wrap(("conv", id(engine)), engine, self.fallback, "conversation")

    @property
    def routing(self) -> Any:
        """Routing and classification: small, fast and on a separate Groq quota."""
        if not self.active:
            return self.default
        primary = self._engine("groq", self.routing_model)
        return self._wrap(("routing",), primary, self.fallback or self.default, "routing")

    classify = routing

    def rag_for(self, engine: Any) -> Any:
        """Answers grounded in retrieved documents: gpt-4o, with the agent's own engine racing it if slow."""
        if not self.active:
            return engine
        primary = self._engine("openai", self.rag_model) if (self.enabled and self.rag_enabled) else None
        if primary is None:
            return self.conversation_for(engine)
        return self._wrap(("rag", id(engine)), primary, engine, "rag", hedge_s=self.rag_hedge_s)

    @property
    def background(self) -> Any:
        """Post-call jobs: latency does not matter, quota and accuracy do."""
        primary = self._engine("groq", self.background_model)
        return self._wrap(("background",), primary or self.fallback, self.fallback if primary else None, "background")
