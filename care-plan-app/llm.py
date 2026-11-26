"""One place that talks to Gemini, on the current google-genai library (google-generativeai has reached end of support).

Everything else calls generate(); it tries each model in turn, gives each its own time limit, and stops when the overall deadline
is near, so one stalled model never holds a request open past the tunnel's 100 s limit.
"""
from __future__ import annotations

import os
import time
from typing import Any, List, Optional, Sequence

from dotenv import load_dotenv
from google import genai
from google.genai import types

import usage_log


def api_key() -> str:
    load_dotenv(override=True)
    return (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()


class LLMUnavailable(RuntimeError):
    """No model answered in time (or none is configured)."""


def generate(contents: Any, models: Sequence[str], *, system: Optional[str] = None, temperature: float = 0.1,
             max_tokens: int = 4096, json_mode: bool = False, per_model_timeout: float = 25.0, deadline: float = 85.0,
             on_attempt=None, purpose: str = "") -> str:
    """Return the text of the first model that answers. `contents` is a string, a list of strings and PIL images, or a list of
    types.Content. `on_attempt(model, seconds, error_or_None)` is called after each try, for logging. Every answered call, even an
    empty one, is billed, so its tokens go to the usage log (usage_log.py) under `purpose` ("ocr", "careplan", "chat")."""
    key = api_key()
    if not key:
        raise LLMUnavailable("no GEMINI_API_KEY is set")
    started, last = time.time(), None
    for name in models:
        left = deadline - (time.time() - started)
        if left < 5:
            break
        for attempt in (0, 1):
            t0 = time.time()
            try:
                client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=int(min(per_model_timeout, left) * 1000)))
                cfg = types.GenerateContentConfig(
                    system_instruction=system, temperature=temperature, max_output_tokens=max_tokens,
                    response_mime_type="application/json" if json_mode else None)
                resp = client.models.generate_content(model=name, contents=contents, config=cfg)
                usage_log.record(purpose, name, getattr(resp, "usage_metadata", None), time.time() - t0)
                text = (resp.text or "").strip()
                if on_attempt:
                    on_attempt(name, time.time() - t0, None)
                if text:
                    return text
                break
            except Exception as exc:                 # timeouts, 5xx, bad model names: try again once if it is a spike, else the next model
                last = exc
                if on_attempt:
                    on_attempt(name, time.time() - t0, exc)
                left = deadline - (time.time() - started)
                if attempt == 0 and _is_spike(exc) and left > 10:
                    time.sleep(1.5)
                    continue
                break
    raise LLMUnavailable(f"no model answered in time ({last.__class__.__name__ if last else 'no time left'})")


def _is_spike(exc: Exception) -> bool:
    """A busy model (503, 429) usually recovers in a second or two; a timeout or a bad request does not."""
    text = str(exc)
    return "503" in text or "429" in text or "UNAVAILABLE" in text or "RESOURCE_EXHAUSTED" in text


def chat_contents(turns: List[dict]) -> list:
    """[{role: user|assistant, text}] to the library's Content objects."""
    return [types.Content(role="user" if t["role"] == "user" else "model", parts=[types.Part(text=t["text"])]) for t in turns]


def part_from_bytes(data: bytes, mime_type: str):
    """A PDF or other file as a content part for a multimodal call."""
    return types.Part.from_bytes(data=data, mime_type=mime_type)
