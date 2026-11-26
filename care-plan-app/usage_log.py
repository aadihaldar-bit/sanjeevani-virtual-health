"""Records the token counts Gemini reports for each call, so what the app uses, and what it costs, can be added up afterwards.

One JSON line per call goes to data/llm_usage.jsonl, on the app's data volume so it survives a container rebuild (LLM_USAGE_LOG moves
it). Gemini bills thinking tokens as output, so they are kept in their own field. Logging never raises: a failed write must not fail a
patient's request.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Optional

_lock = threading.Lock()


def _path() -> str:
    return os.getenv("LLM_USAGE_LOG", os.path.join("data", "llm_usage.jsonl"))


def _count(usage: Any, snake: str, camel: str) -> int:
    """One counter from google-genai's usage_metadata (an object) or the REST response's usageMetadata (a dict); absent is 0."""
    value = usage.get(camel) if isinstance(usage, dict) else getattr(usage, snake, None)
    return int(value or 0)


def record(purpose: str, model: str, usage: Any, seconds: Optional[float] = None) -> None:
    """Append one call's tokens. `usage` is usage_metadata / usageMetadata; None (an error, a stand-in client) records nothing."""
    if usage is None:
        return
    try:
        row = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "purpose": purpose or "unknown", "model": model,
            "input": _count(usage, "prompt_token_count", "promptTokenCount"),
            "output": _count(usage, "candidates_token_count", "candidatesTokenCount"),
            "thoughts": _count(usage, "thoughts_token_count", "thoughtsTokenCount"),
            "cached": _count(usage, "cached_content_token_count", "cachedContentTokenCount"),
            "total": _count(usage, "total_token_count", "totalTokenCount"),
            "seconds": round(seconds, 2) if seconds is not None else None,
        }
        print(f" -> [Tokens] {row['purpose']} {model}: in {row['input']}, out {row['output']}, thinking {row['thoughts']}")
        path = _path()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with _lock, open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except Exception as exc:  # noqa: BLE001 - usage logging is best effort
        print(f" [!] [Token log] could not record usage: {exc}")
