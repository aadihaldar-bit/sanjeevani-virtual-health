"""
Langfuse Tracer Module — Non-Blocking Telemetry & LLM Observability
Captures voice agent turns, model generations, tool executions, and metadata
asynchronously with 0.00ms latency impact on live audio streaming.
"""

import os
import re
import asyncio
import logging
import contextvars
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .config import AppConfig, get_config

logger = logging.getLogger(__name__)

# Context variable to capture token usage across asynchronous tasks
_last_turn_usage: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar("_last_turn_usage", default=None)


def record_llm_usage(
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: Optional[int] = None,
    model: Optional[str] = None
) -> None:
    """Accumulates token usage of LLM calls in the current async task context."""
    p = int(prompt_tokens or 0)
    c = int(completion_tokens or 0)
    t = int(total_tokens) if total_tokens is not None else (p + c)

    current = _last_turn_usage.get()
    if current is None:
        _last_turn_usage.set({
            "input": p,
            "output": c,
            "total": t,
            "model": model
        })
    else:
        _last_turn_usage.set({
            "input": current.get("input", 0) + p,
            "output": current.get("output", 0) + c,
            "total": current.get("total", 0) + t,
            "model": model or current.get("model")
        })


def get_last_llm_usage() -> Optional[Dict[str, Any]]:
    """Returns the accumulated token usage for the current context."""
    return _last_turn_usage.get()


def reset_turn_usage() -> None:
    """Resets the accumulated token usage context."""
    _last_turn_usage.set(None)


def estimate_tokens(text: Optional[str]) -> int:
    """Heuristic token estimation: ~4 chars per token, minimum 1 if text is non-empty."""
    if not text:
        return 0
    clean = str(text).strip()
    if not clean:
        return 0
    return max(1, len(clean) // 4)


# Default USD cost per 1M tokens: (input_price, output_price)
MODEL_PRICING_PER_1M: Dict[str, Tuple[float, float]] = {
    # Groq / LLaMA
    "llama-3.3-70b-versatile": (0.59, 0.79),
    "llama-3.3-70b": (0.59, 0.79),
    "llama-3.1-70b-versatile": (0.59, 0.79),
    "llama-3.1-8b-instant": (0.05, 0.08),
    "llama3-70b-8192": (0.59, 0.79),
    "llama3-8b-8192": (0.05, 0.08),
    "openai/gpt-oss-120b": (0.60, 0.80),
    "openai/gpt-oss-20b": (0.15, 0.25),
    "mixtral-8x7b-32768": (0.24, 0.24),

    # Google Gemini
    "gemini-2.5-flash": (0.075, 0.30),
    "gemini-3.6-flash": (0.075, 0.30),
    "gemini-1.5-flash": (0.075, 0.30),
    "gemini-1.5-pro": (1.25, 5.00),
    "gemini-flash-latest": (0.075, 0.30),

    # OpenAI
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
}


def calculate_cost_details(model_name: Optional[str], input_tokens: int, output_tokens: int) -> Dict[str, float]:
    """Calculates estimated USD cost for model generation to ensure Langfuse reflects costs."""
    if not model_name or str(model_name).lower() in ("greeting", "none", "static", "span"):
        return {"input": 0.0, "output": 0.0, "total": 0.0}

    m_clean = str(model_name).lower().replace("models/", "").strip()

    rates = None
    for pattern, rate_pair in MODEL_PRICING_PER_1M.items():
        if pattern.lower() in m_clean or m_clean in pattern.lower():
            rates = rate_pair
            break

    if not rates:
        rates = (0.50, 0.80)  # Conservative fallback

    in_cost = round((input_tokens / 1_000_000.0) * rates[0], 7)
    out_cost = round((output_tokens / 1_000_000.0) * rates[1], 7)
    total_cost = round(in_cost + out_cost, 7)

    return {
        "input": in_cost,
        "output": out_cost,
        "total": total_cost
    }


try:
    import langfuse
    from langfuse import Langfuse
    HAS_LANGFUSE = True
except ImportError:
    HAS_LANGFUSE = False
    logger.warning("[LangfuseTracer] langfuse package not installed. Tracing is disabled.")


def _clean_phone_number(phone: Optional[str]) -> str:
    """Normalizes phone numbers for consistent session and file labels."""
    if not phone:
        return "unknown"
    p = str(phone).replace("whatsapp:", "").strip()
    p = re.sub(r'[^0-9+]', '', p)
    return p if p else "unknown"


class LangfuseTracer:
    """
    Central Langfuse LLM Observability Tracer.
    Executes all trace, observation, and generation dispatches completely in background
    threads (fire-and-forget) to ensure zero latency impact on speech-to-speech audio pipelines.
    """

    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or get_config()
        self.settings = getattr(self.config, "langfuse", None)
        self.client: Optional[Any] = None
        self._initialized = False

        self._init_client()

    def _init_client(self):
        """Initializes the Langfuse client if enabled and credentials exist."""
        if not HAS_LANGFUSE:
            return

        enabled = getattr(self.settings, "enabled", True) if self.settings else True
        if not enabled:
            logger.info("[LangfuseTracer] Tracing explicitly disabled via configuration.")
            return

        public_key = getattr(self.settings, "public_key", "") or os.getenv("LANGFUSE_PUBLIC_KEY", "")
        secret_key = getattr(self.settings, "secret_key", "") or os.getenv("LANGFUSE_SECRET_KEY", "")
        host = getattr(self.settings, "host", "") or os.getenv("LANGFUSE_HOST", os.getenv("LANGFUSE_BASE_URL", "https://internalagent.stldigitaltech.com"))

        if not public_key or not secret_key:
            logger.warning("[LangfuseTracer] LANGFUSE_PUBLIC_KEY or LANGFUSE_SECRET_KEY missing. Tracing disabled.")
            return

        try:
            self.client = Langfuse(
                public_key=public_key,
                secret_key=secret_key,
                host=host,
                release=getattr(self.settings, "release", "v1.0.0")
            )
            self._initialized = True
            logger.info("[LangfuseTracer] Connected successfully to Langfuse host: %s", host)
        except Exception as exc:
            logger.warning("[LangfuseTracer] Failed to initialize Langfuse client: %s", exc)
            self.client = None

    def trace_turn_background(
        self,
        session_id: str,
        agent_name: str,
        user_message: str,
        assistant_response: str,
        model: Optional[str] = None,
        language_code: Optional[str] = None,
        provider: Optional[str] = None,
        caller_phone: Optional[str] = None,
        call_id: Optional[Any] = None,
        call_sid: Optional[str] = None,
        node_id: Optional[str] = None,
        voice: Optional[str] = None,
        tool_called: Optional[str] = None,
        tool_args: Optional[Dict[str, Any]] = None,
        tool_result: Optional[Any] = None,
        latency_ms: Optional[float] = None,
        extra_metadata: Optional[Dict[str, Any]] = None,
        usage_details: Optional[Dict[str, int]] = None,
        cost_details: Optional[Dict[str, float]] = None,
    ) -> None:
        """
        Dispatches a turn observation to Langfuse completely in the background.
        Returns immediately (0.00ms delay to audio pipeline).
        """
        if not self._initialized or not self.client:
            return

        # Clean session ID format: call_{call_id}_{provider}_{phone} if available
        prov = (provider or "telephony").lower()
        clean_phone = _clean_phone_number(caller_phone)
        formatted_session_id = session_id
        if call_id and "call_" not in session_id:
            formatted_session_id = f"call_{call_id}_{prov}_{clean_phone}"

        lang = language_code or getattr(self.config.stt, "language_code", "en-IN")

        # Resolve model name (handle generic placeholders like 'groq' or 'groq-llm')
        model_name = model
        if not model_name or str(model_name).lower() in ("groq", "groq-llm"):
            model_name = getattr(self.config.llm, "groq_model", "openai/gpt-oss-120b")

        # Retrieve or compute token usage details
        if usage_details is None:
            last_usage = get_last_llm_usage()
            if last_usage:
                usage_details = {
                    "input": int(last_usage.get("input", 0)),
                    "output": int(last_usage.get("output", 0)),
                    "total": int(last_usage.get("total", 0)),
                }
                if last_usage.get("model") and model in (None, "", "groq", "groq-llm"):
                    model_name = last_usage.get("model")
            reset_turn_usage()

        if usage_details is None or (usage_details.get("input", 0) == 0 and usage_details.get("output", 0) == 0):
            if str(model_name).lower() == "greeting":
                usage_details = {"input": 0, "output": 0, "total": 0}
            else:
                inp_tok = estimate_tokens(user_message)
                out_tok = estimate_tokens(assistant_response)
                usage_details = {
                    "input": inp_tok,
                    "output": out_tok,
                    "total": inp_tok + out_tok,
                }

        # Calculate cost details if not explicitly provided
        if cost_details is None:
            cost_details = calculate_cost_details(
                model_name=model_name,
                input_tokens=usage_details.get("input", 0),
                output_tokens=usage_details.get("output", 0),
            )

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                asyncio.to_thread(
                    self._record_turn_sync,
                    formatted_session_id,
                    agent_name,
                    user_message,
                    assistant_response,
                    model_name,
                    lang,
                    prov,
                    clean_phone,
                    call_id,
                    call_sid,
                    node_id,
                    voice,
                    tool_called,
                    tool_args,
                    tool_result,
                    latency_ms,
                    extra_metadata,
                    usage_details,
                    cost_details,
                )
            )
        except RuntimeError:
            # Fallback if called outside of an active asyncio event loop
            import threading
            t = threading.Thread(
                target=self._record_turn_sync,
                args=(
                    formatted_session_id,
                    agent_name,
                    user_message,
                    assistant_response,
                    model_name,
                    lang,
                    prov,
                    clean_phone,
                    call_id,
                    call_sid,
                    node_id,
                    voice,
                    tool_called,
                    tool_args,
                    tool_result,
                    latency_ms,
                    extra_metadata,
                    usage_details,
                    cost_details,
                ),
                daemon=True,
            )
            t.start()
        except Exception as exc:
            logger.debug("[LangfuseTracer] Failed to dispatch background task: %s", exc)

    def _record_turn_sync(
        self,
        session_id: str,
        agent_name: str,
        user_message: str,
        assistant_response: str,
        model: Optional[str],
        language_code: str,
        provider: str,
        caller_phone: str,
        call_id: Optional[Any],
        call_sid: Optional[str],
        node_id: Optional[str],
        voice: Optional[str],
        tool_called: Optional[str],
        tool_args: Optional[Dict[str, Any]],
        tool_result: Optional[Any],
        latency_ms: Optional[float],
        extra_metadata: Optional[Dict[str, Any]],
        usage_details: Optional[Dict[str, int]] = None,
        cost_details: Optional[Dict[str, float]] = None,
    ) -> None:
        """Internal synchronous worker executing inside a background thread pool."""
        try:
            tags = [
                "voice-agent",
                provider,
                language_code,
                agent_name
            ]
            # Deduplicate tags preserving order
            seen_tags = set()
            clean_tags = [t for t in tags if t and not (t in seen_tags or seen_tags.add(t))]

            meta: Dict[str, Any] = {
                "language_code": language_code,
                "provider": provider,
                "caller_phone": caller_phone,
                "call_id": call_id,
                "call_sid": call_sid,
                "node_id": node_id,
                "voice": voice,
            }
            if latency_ms is not None:
                meta["latency_ms"] = round(latency_ms, 2)
            if extra_metadata:
                meta.update(extra_metadata)

            model_name = model or getattr(self.config.llm, "groq_model", "openai/gpt-oss-20b")

            with langfuse.propagate_attributes(
                session_id=session_id,
                tags=clean_tags,
                metadata=meta,
                trace_name=f"{agent_name} Turn"
            ):
                # If a tool was executed during this turn, record it as a tool observation
                if tool_called:
                    try:
                        tool_obs = self.client.start_observation(
                            name=tool_called,
                            as_type="tool",
                            input=tool_args or {},
                            output=tool_result,
                            metadata={"language_code": language_code, "call_id": call_id}
                        )
                        tool_obs.end()
                    except Exception as t_err:
                        logger.debug("[LangfuseTracer] Tool observation error: %s", t_err)

                # Record the generation or span observation
                if str(model_name).lower() == "greeting":
                    span_obs = self.client.start_observation(
                        name=f"{agent_name} (Greeting)",
                        as_type="span",
                        input=user_message,
                        output=assistant_response,
                        metadata={"language_code": language_code, "voice": voice, "node_id": node_id}
                    )
                    span_obs.end()
                else:
                    gen_obs = self.client.start_observation(
                        name=agent_name,
                        as_type="generation",
                        model=model_name,
                        input=user_message,
                        output=assistant_response,
                        usage_details=usage_details,
                        cost_details=cost_details,
                        metadata={"language_code": language_code, "voice": voice, "node_id": node_id}
                    )
                    gen_obs.end()

        except Exception as exc:
            logger.debug("[LangfuseTracer] Error recording turn to Langfuse: %s", exc)

    async def flush_async(self) -> None:
        """Flushes buffered traces to Langfuse (e.g. at app shutdown)."""
        if self._initialized and self.client:
            try:
                await asyncio.to_thread(self.client.flush)
            except Exception as exc:
                logger.debug("[LangfuseTracer] Flush error: %s", exc)


# Global singleton instance
langfuse_tracer = LangfuseTracer()
