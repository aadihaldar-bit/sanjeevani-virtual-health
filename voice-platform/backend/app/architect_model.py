"""The model Architect thinks with: its chat, and the drawing of an agent from a description.

Nobody is waiting on a phone line here, so this is the strongest thinking model the platform's Gemini key reaches, and it is given
time. If that model cannot answer, the next one in ARCHITECT_MODELS tries, then the platform's own engine (when that one reports
its failures instead of hiding them). If nothing can answer, ArchitectModelError says so: a sentence made up in a model's place
reads like an answer, and the console would build on it.
"""
import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import httpx

from .config import AppConfig, get_config

logger = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_STREAM_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse"
THINKING_ROOM = 16384       # a thinking model spends part of its output allowance thinking; the answer needs its own on top
MIN_ATTEMPT_SECS = 5.0      # not worth starting another model with less than this left (the time limits are in the configuration)


class ArchitectModelError(RuntimeError):
    """No model could answer."""


def _turns(history: List[Dict[str, str]], prompt: str) -> List[Dict[str, Any]]:
    """The conversation in Gemini's shape: turns are "user" or "model", they start with the person, and neighbours of one kind are one."""
    turns: List[Dict[str, Any]] = []
    for message in [*history, {"role": "user", "content": prompt}]:
        role = "model" if message.get("role") == "assistant" else "user"
        text = str(message.get("content") or "").strip()
        if not text or (not turns and role == "model"):
            continue
        if turns and turns[-1]["role"] == role:
            turns[-1]["parts"][0]["text"] += "\n\n" + text
        else:
            turns.append({"role": role, "parts": [{"text": text}]})
    return turns


def _why(exc: BaseException) -> str:
    """What went wrong, in words: a timeout carries no message of its own, so it is named."""
    return str(exc) or type(exc).__name__


def _complaint(res: httpx.Response) -> str:
    try:
        return str(res.json()["error"]["message"])[:200]
    except (ValueError, KeyError, TypeError):
        return res.text[:200]


def _checked(text: str, finish: str) -> str:
    """The reply, or ArchitectModelError when it is cut off or empty."""
    if finish == "MAX_TOKENS":
        raise ArchitectModelError("ran out of room before it finished")              # a cut-off document is worse than none
    if not text:
        raise ArchitectModelError(f"gave no answer (finish: {finish})")
    return text


async def _heard(on_thought: Callable[[str], Awaitable[None]], text: str) -> None:
    try:
        await on_thought(text)
    except Exception:                                                                # noqa: BLE001  showing the thinking must never cost the answer
        logger.debug("[Architect] the thinking could not be shown", exc_info=True)


async def _read_stream(res: httpx.Response, on_thought: Callable[[str], Awaitable[None]]) -> Tuple[str, str]:
    """The answer and the reason it stopped, from a streamed reply. Each summary of the model's thinking goes to `on_thought` as it comes."""
    answer: List[str] = []
    finish = blocked = ""
    async for line in res.aiter_lines():
        if not line.startswith("data:"):
            continue
        try:
            data = json.loads(line[5:].strip())
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        candidate = (data.get("candidates") or [{}])[0]
        finish = candidate.get("finishReason") or finish
        blocked = (data.get("promptFeedback") or {}).get("blockReason") or blocked
        for part in (candidate.get("content") or {}).get("parts") or []:
            if isinstance(part, dict) and part.get("text"):
                if part.get("thought"):
                    await _heard(on_thought, part["text"])
                else:
                    answer.append(part["text"])
    return "".join(answer).strip(), finish or blocked or "unknown"


async def _stream_gemini(model: str, key: str, body: Dict[str, Any], timeout: float, on_thought: Callable[[str], Awaitable[None]]) -> str:
    """The same question as `_ask_gemini` asks, answered as a stream, so the model's own summaries of its thinking can be shown while it works."""
    config, url, headers = body["generationConfig"], GEMINI_STREAM_URL.format(model=model), {"x-goog-api-key": key}

    async def once() -> str:
        async with httpx.AsyncClient(timeout=timeout) as client:
            while True:
                async with client.stream("POST", url, headers=headers, json=body) as res:
                    if res.status_code != 200:
                        await res.aread()
                        thinking = config.get("thinkingConfig")
                        if res.status_code == 400 and "thinking" in res.text.lower() and thinking:
                            if thinking.pop("includeThoughts", None) is None:
                                del config["thinkingConfig"]                          # a model that thinks its own way, and will not show it
                            continue
                        raise ArchitectModelError(f"answered {res.status_code}: {_complaint(res)}")
                    text, finish = await _read_stream(res, on_thought)
                    return _checked(text, finish)

    try:
        return await asyncio.wait_for(once(), timeout)
    except asyncio.TimeoutError:
        raise ArchitectModelError(f"took longer than {timeout:.0f} seconds")


async def _ask_gemini(model: str, key: str, system_prompt: str, turns: List[Dict[str, Any]], answer_tokens: int,
                      thinking: str, timeout: float, json_output: bool = True,
                      on_thought: Optional[Callable[[str], Awaitable[None]]] = None) -> str:
    """One model's reply as text. Raises ArchitectModelError when it could not give a whole one. With `on_thought`, the reply is streamed and
    each summary of the model's thinking is handed to it as it arrives."""
    config: Dict[str, Any] = {"maxOutputTokens": answer_tokens + THINKING_ROOM}
    if json_output:
        config["responseMimeType"] = "application/json"
    thinking_config: Dict[str, Any] = {"thinkingLevel": thinking} if thinking else {}
    if on_thought:
        thinking_config["includeThoughts"] = True                                     # ask for the summaries, to show while it works
    if thinking_config:
        config["thinkingConfig"] = thinking_config
    body = {"systemInstruction": {"parts": [{"text": system_prompt}]}, "contents": turns, "generationConfig": config}
    if on_thought:
        return await _stream_gemini(model, key, body, timeout, on_thought)
    url, headers = GEMINI_URL.format(model=model), {"x-goog-api-key": key}         # the key travels in a header, never in the address
    async with httpx.AsyncClient(timeout=timeout) as client:
        res = await client.post(url, headers=headers, json=body)
        if res.status_code == 400 and "thinking" in res.text.lower() and "thinkingConfig" in config:
            del config["thinkingConfig"]                                              # a model that thinks its own way
            res = await client.post(url, headers=headers, json=body)
    if res.status_code != 200:
        raise ArchitectModelError(f"answered {res.status_code}: {_complaint(res)}")
    try:
        data = res.json()
    except ValueError:
        raise ArchitectModelError("sent something that is not JSON")
    candidate = (data.get("candidates") or [{}])[0]
    finish = candidate.get("finishReason") or (data.get("promptFeedback") or {}).get("blockReason") or "unknown"
    parts = (candidate.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought")).strip()
    return _checked(text, finish)


async def _ask_engine(cfg: AppConfig, system_prompt: str, history: List[Dict[str, str]], prompt: str, answer_tokens: int) -> str:
    from .ai_services import LLMEngineFactory, generate_raw
    engine = LLMEngineFactory.get_engine(cfg)
    if not getattr(engine, "supports_strict", False):
        raise ArchitectModelError("the platform's language model swallows its own failures, so it cannot stand in")
    return await generate_raw(engine, prompt, history, system_prompt, answer_tokens)


async def ask(system_prompt: str, prompt: str, history: Optional[List[Dict[str, str]]] = None, *,
              cfg: Optional[AppConfig] = None, answer_tokens: int = 700, models: Optional[List[str]] = None,
              thinking: Optional[str] = None, attempt_secs: Optional[float] = None, total_secs: Optional[float] = None,
              json_output: bool = True, on_thought: Optional[Callable[[str], Awaitable[None]]] = None) -> str:
    """The model's reply to `prompt` after `history`: a JSON document, or plain text when `json_output` is off (a written document).
    `answer_tokens` is how long the reply itself needs to be. `cfg` is the configuration to use (an organization's own keys applied);
    the platform's when left out. The rest overrides what the configuration says for one kind of job: which models to try, how hard
    they think, and how long one attempt and all of them may take (a web request has one minute, and a document is quicker than a design).
    `on_thought` receives the Gemini models' own summaries of their thinking while they work (the platform's engine has none to show)."""
    cfg = cfg or get_config()
    history = list(history or [])
    started = time.monotonic()
    problems: List[str] = []
    total = cfg.llm.architect_total_secs if total_secs is None else total_secs
    attempt = cfg.llm.architect_attempt_secs if attempt_secs is None else attempt_secs
    level = cfg.llm.architect_thinking if thinking is None else thinking
    if cfg.llm.gemini_api_key:
        turns = _turns(history, prompt)
        for model in (models or cfg.llm.architect_models):
            left = total - (time.monotonic() - started)
            if left < MIN_ATTEMPT_SECS:
                problems.append("out of time")
                break
            try:
                return await _ask_gemini(model, cfg.llm.gemini_api_key, system_prompt, turns, answer_tokens, level, min(attempt, left), json_output,
                                         on_thought)
            except (ArchitectModelError, httpx.HTTPError) as exc:
                problems.append(f"{model} {_why(exc)}")
                logger.warning("[Architect] %s could not answer: %s", model, _why(exc))
    else:
        problems.append("no Gemini key is set")
    try:
        return await _ask_engine(cfg, system_prompt, history, prompt, answer_tokens)
    except Exception as exc:                                                         # noqa: BLE001  any engine may fail its own way
        problems.append(f"the platform's engine: {_why(exc)}")
        logger.warning("[Architect] the platform's engine could not answer either: %s", _why(exc))
    raise ArchitectModelError("; ".join(problems))
