"""
Smoke tests — run before every deploy to catch import errors, attribute
collisions, and broken service wiring.

Usage:
    pytest tests/test_smoke.py -v              # from backend/
    pytest backend/tests/test_smoke.py -v      # from repo root
"""

import importlib
import inspect
import sys
import os
import asyncio
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Support running from repo root (backend.app.*) or from backend/ (app.*)
def _import(name):
    try:
        return importlib.import_module(f"backend.{name}")
    except ModuleNotFoundError:
        return importlib.import_module(name)

APP_MODULES = [
    "app.config",
    "app.models",
    "app.database",
    "app.interfaces",
    "app.audio_services",
    "app.ai_services",
    "app.agent_router",
    "app.tool_executor",
    "app.rag_engine",
    "app.event_logger",
    "app.langfuse_tracer",
    "app.websocket_manager",
    "app.summit_mcp_server",
    "app.pipecat_processors",
    "app.media_bridge",
]

# ---------------------------------------------------------------------------
# 1. All backend modules import cleanly
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("module_name", APP_MODULES)
def test_module_imports(module_name):
    """Every backend module must import without raising."""
    mod = _import(module_name)
    assert mod is not None


# ---------------------------------------------------------------------------
# 2. Pipecat reserved-attribute guard
# ---------------------------------------------------------------------------

def test_no_pipecat_reserved_attribute_collision():
    """
    Pipecat's FrameProcessor uses internal attributes (_metrics, _event_handlers,
    etc.) that subclasses must not shadow. This test catches collisions at class
    definition time — the bug that killed calls 124 & 125.
    """
    from pipecat.processors.frame_processor import FrameProcessor

    reserved = set()
    for name, val in inspect.getmembers(FrameProcessor):
        if name.startswith("_") and not name.startswith("__"):
            reserved.add(name)

    procs = _import("app.pipecat_processors")
    for cls in (procs.SmartfloSTTProcessor, procs.AgentRouterProcessor, procs.SmartfloTTSProcessor):
        init_src = inspect.getsource(cls.__init__)
        for attr in reserved:
            pattern = f"self.{attr}"
            if pattern in init_src and f"self.{attr}_" not in init_src:
                pytest.fail(
                    f"{cls.__name__}.__init__ sets self.{attr} which collides "
                    f"with FrameProcessor's internal attribute. "
                    f"Use a different name (e.g. self._call_{attr[1:]})."
                )


# ---------------------------------------------------------------------------
# 3. CallMetricsCollector has no overlap with FrameProcessor
# ---------------------------------------------------------------------------

def test_call_metrics_collector_is_not_frame_processor():
    """CallMetricsCollector must NOT inherit from FrameProcessor."""
    procs = _import("app.pipecat_processors")
    from pipecat.processors.frame_processor import FrameProcessor
    assert not issubclass(procs.CallMetricsCollector, FrameProcessor), \
        "CallMetricsCollector must be a plain class, not a FrameProcessor"


# ---------------------------------------------------------------------------
# 4. Processor lifecycle doesn't crash
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_processors_instantiate():
    """All three processors can be instantiated without error."""
    procs = _import("app.pipecat_processors")
    cfg_mod = _import("app.config")

    cfg = cfg_mod.get_config()
    metrics = procs.CallMetricsCollector()

    state = {"is_agent_speaking": False, "interrupted": False}
    stt = procs.SmartfloSTTProcessor(stt_service=MagicMock(), config=cfg, session_state_ref=state,
                                     metrics_collector=metrics)
    assert stt is not None

    router_proc = procs.AgentRouterProcessor(
        router=MagicMock(), config=cfg, session_state_ref=state, db_call_id_ref=[None], call_sid_ref=[None],
        caller_phone_ref=[None], stream_sid_ref=[None], chat_history=[], current_node_id_ref=[None],
        transcript_sequence_ref=[1], telephony_service=MagicMock(), metrics_collector=metrics,
    )
    assert router_proc is not None

    tts_proc = procs.SmartfloTTSProcessor(
        tts_service=MagicMock(), config=cfg, session_state_ref=state, call_sid_ref=[None],
        telephony_service=MagicMock(), metrics_collector=metrics,
    )
    assert tts_proc is not None


# ---------------------------------------------------------------------------
# 5. Config loads without missing required env vars
# ---------------------------------------------------------------------------

def test_config_loads():
    cfg_mod = _import("app.config")
    cfg = cfg_mod.get_config()
    assert cfg is not None
    assert cfg.stt.sample_rate == 16000


# ---------------------------------------------------------------------------
# 6. SummitAI MCP tools are registered
# ---------------------------------------------------------------------------

def test_summit_mcp_tools_exist():
    mcp = _import("app.summit_mcp_server")
    assert asyncio.iscoroutinefunction(mcp.create_incident)
    assert asyncio.iscoroutinefunction(mcp.list_incidents)
    assert asyncio.iscoroutinefunction(mcp.get_incident_description)
    assert asyncio.iscoroutinefunction(mcp.update_incident)


# ---------------------------------------------------------------------------
# 7. Event logger has required methods
# ---------------------------------------------------------------------------

def test_event_logger_interface():
    el = _import("app.event_logger")
    assert hasattr(el.event_logger, "emit")
    assert hasattr(el.event_logger, "flush_to_gcs")


# ---------------------------------------------------------------------------
# 8. Pipeline ordering sanity — processors are wired in the right order
# ---------------------------------------------------------------------------

def test_pipeline_imports_match_expected_order():
    """The media_bridge module must reference all three processor classes."""
    mb = _import("app.media_bridge")
    src = inspect.getsource(mb)
    assert "SmartfloSTTProcessor" in src
    assert "AgentRouterProcessor" in src
    assert "SmartfloTTSProcessor" in src


# ---------------------------------------------------------------------------
# 9. No hardcoded secrets in source
# ---------------------------------------------------------------------------

SECRET_PATTERNS = ["sk-", "gsk_", "AIza", "AKIA"]

@pytest.mark.parametrize("module_name", APP_MODULES)
def test_no_hardcoded_secrets(module_name):
    mod = _import(module_name)
    src_file = getattr(mod, "__file__", None)
    if not src_file or not os.path.isfile(src_file):
        pytest.skip("no source file")
    with open(src_file, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    for pat in SECRET_PATTERNS:
        lines = [
            (i + 1, line.strip())
            for i, line in enumerate(content.splitlines())
            if pat in line
            and "os.getenv" not in line
            and "os.environ" not in line
            and "#" not in line.split(pat)[0][-10:]
            and "example" not in line.lower()
        ]
        assert not lines, (
            f"Possible hardcoded secret ({pat}...) in {module_name} "
            f"at line(s): {lines[:3]}"
        )
