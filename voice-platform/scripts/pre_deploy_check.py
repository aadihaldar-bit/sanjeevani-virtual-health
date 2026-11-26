#!/usr/bin/env python3
"""
Pre-deploy health check — run on the VM before restarting containers.

Usage:
    python scripts/pre_deploy_check.py          # from repo root
    ssh vm 'cd /opt/stl-voicebot && python scripts/pre_deploy_check.py'

Exits 0 if everything passes, 1 on any failure.
"""

import sys
import os
import json
import importlib
import inspect
import subprocess
import socket
import re

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
WARN = "\033[93m!\033[0m"

errors = []
warnings = []


def check(label: str, ok: bool, detail: str = ""):
    if ok:
        print(f"  {PASS} {label}")
    else:
        msg = f"{label}: {detail}" if detail else label
        errors.append(msg)
        print(f"  {FAIL} {msg}")


def warn(label: str, detail: str = ""):
    msg = f"{label}: {detail}" if detail else label
    warnings.append(msg)
    print(f"  {WARN} {msg}")


# ── 1. Python imports ──────────────────────────────────────────────────────

print("\n[1/7] Module imports")
MODULES = [
    "backend.app.config",
    "backend.app.pipecat_processors",
    "backend.app.media_bridge",
    "backend.app.event_logger",
    "backend.app.summit_mcp_server",
    "backend.app.agent_router",
    "backend.app.models",
]
for mod in MODULES:
    try:
        importlib.import_module(mod)
        check(mod, True)
    except Exception as e:
        check(mod, False, str(e)[:120])


# ── 2. Pipecat attribute collision ─────────────────────────────────────────

print("\n[2/7] Pipecat attribute safety")
try:
    from pipecat.processors.frame_processor import FrameProcessor
    reserved = {
        n for n in dir(FrameProcessor)
        if n.startswith("_") and not n.startswith("__")
    }
    from backend.app.pipecat_processors import (
        SmartfloSTTProcessor, AgentRouterProcessor, SmartfloTTSProcessor,
    )
    collision_found = False
    for cls in (SmartfloSTTProcessor, AgentRouterProcessor, SmartfloTTSProcessor):
        src = inspect.getsource(cls.__init__)
        for attr in reserved:
            if f"self.{attr}" in src and f"self.{attr}_" not in src:
                check(f"{cls.__name__}.{attr}", False,
                      f"shadows FrameProcessor.{attr}")
                collision_found = True
    if not collision_found:
        check("No reserved-attribute collisions", True)
except Exception as e:
    check("Pipecat attribute check", False, str(e)[:120])


# ── 3. Required environment variables ──────────────────────────────────────

print("\n[3/7] Environment variables")
REQUIRED_ENV = [
    "DATABASE_URL",
    "GROQ_API_KEY",
    "CARTESIA_API_KEY",
    "GCS_BUCKET_NAME",
]
OPTIONAL_ENV = [
    "SUMMIT_API_KEY",
    "DEEPGRAM_API_KEY",
    "LANGFUSE_PUBLIC_KEY",
    "SARVAM_API_KEY",
]
for var in REQUIRED_ENV:
    val = os.environ.get(var, "")
    check(var, bool(val), "not set or empty")

for var in OPTIONAL_ENV:
    val = os.environ.get(var, "")
    if not val:
        warn(var, "not set (optional)")
    else:
        check(var, True)


# ── 4. Docker containers ──────────────────────────────────────────────────

print("\n[4/7] Docker containers")
try:
    out = subprocess.check_output(
        ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}"],
        text=True, timeout=10
    )
    expected = ["stl-voicebot-backend", "voice_agent_postgres", "voice_agent_redis"]
    running = {line.split("\t")[0] for line in out.strip().splitlines() if line}
    for name in expected:
        check(name, name in running, "not running")
except Exception as e:
    check("Docker check", False, str(e)[:120])


# ── 5. Port connectivity ──────────────────────────────────────────────────

print("\n[5/7] Port connectivity")
PORTS = {
    "Backend (8055)": 8055,
    "Postgres (5432)": 5432,
    "Redis (6379)": 6379,
    "Qdrant (6333)": 6333,
}
for label, port in PORTS.items():
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=3)
        s.close()
        check(label, True)
    except Exception:
        check(label, False, "connection refused or timed out")


# ── 6. GCS credentials ────────────────────────────────────────────────────

print("\n[6/7] GCS credentials")
cred_paths = [
    "/opt/stl-voicebot/secrets/gcs-credentials.json",
    os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", ""),
]
found = False
for p in cred_paths:
    if p and os.path.isfile(p):
        try:
            with open(p) as f:
                data = json.load(f)
            check(f"GCS creds ({p})", "project_id" in data, "missing project_id")
            found = True
            break
        except Exception as e:
            check(f"GCS creds ({p})", False, str(e)[:80])
if not found:
    warn("No GCS credential file found (calls won't log to GCS)")


# ── 7. Disk space ─────────────────────────────────────────────────────────

print("\n[7/7] Disk space")
try:
    st = os.statvfs("/")
    free_gb = (st.f_bavail * st.f_frsize) / (1024 ** 3)
    check(f"Free disk: {free_gb:.1f} GB", free_gb > 1.0, "less than 1 GB free")
except AttributeError:
    warn("Disk check skipped (Windows)")
except Exception as e:
    warn(f"Disk check failed: {e}")


# ── Summary ────────────────────────────────────────────────────────────────

print("\n" + "─" * 50)
if errors:
    print(f"\033[91m{len(errors)} FAILURE(S) — DO NOT DEPLOY\033[0m")
    for e in errors:
        print(f"  ✗ {e}")
    sys.exit(1)
elif warnings:
    print(f"\033[93m{len(warnings)} warning(s), all checks passed\033[0m")
    sys.exit(0)
else:
    print(f"\033[92mAll checks passed — safe to deploy\033[0m")
    sys.exit(0)
