"""Where does a guided-incident turn spend its time?  python e2e/profile_latency.py [scenario]

Wraps the guard, the slot extraction and the reply wording with timers and prints a per-turn breakdown.
"""
import asyncio
import logging
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import harness  # noqa: E402

harness.configure_environment()
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(harness.BACKEND, ".env"))
except Exception:
    pass
logging.disable(logging.CRITICAL)

from scenarios import SCENARIOS  # noqa: E402

PHASES = {}


def timed(obj, name, label):
    orig = getattr(obj, name)

    async def wrapper(*a, **k):
        t = time.perf_counter()
        try:
            return await orig(*a, **k)
        finally:
            PHASES[label] = PHASES.get(label, 0) + int((time.perf_counter() - t) * 1000)

    setattr(obj, name, wrapper)


async def main() -> None:
    wanted = sys.argv[1] if len(sys.argv) > 1 else "incident_printer"
    sc = next(s for s in SCENARIOS if s.name == wanted)
    h = harness.Harness(pace_seconds=3)
    await h.setup()
    from app.incident_flow import IncidentFlow
    timed(IncidentFlow, "extract", "extract")
    timed(IncidentFlow, "compose", "compose")
    timed(IncidentFlow, "create_ticket", "ticket")
    s = await h.new_call(sc.workflow)
    router = s.router
    if getattr(router, "jev_engine", None) and getattr(router.jev_engine, "guard_input", None):
        timed(router.jev_engine, "guard_input", "guard")
    if os.getenv("WARM") == "1":
        t = time.perf_counter()
        await router.roles.classify.generate(prompt="ping", history=[], system_prompt="Reply with {}", tools=None, max_tokens=5, raw=True)
        print("warm-up call took", int((time.perf_counter() - t) * 1000), "ms")
    print(f"{'turn':4} {'total':>6} {'guard':>6} {'extract':>8} {'compose':>8} {'ticket':>7} {'other':>6}  caller")
    for i, text in enumerate(sc.turns, 1):
        PHASES.clear()
        t = await h.say(s, text)
        p = dict(PHASES)
        other = t.ms - sum(p.values())
        print(f"{i:<4} {t.ms:>6} {p.get('guard', 0):>6} {p.get('extract', 0):>8} {p.get('compose', 0):>8} {p.get('ticket', 0):>7} {other:>6}  {text[:46]}")
    print("guard engine:", type(getattr(router, "jev_engine", None)).__name__,
          "| guard_available:", getattr(getattr(router, "jev_engine", None), "guard_available", None))
    print("roles:", {k: getattr(router.roles, k + "_model", None) for k in ("routing", "background", "rag")},
          "conversation:", getattr(router.roles, "conversation_model", None))
    print("speculative wording:", getattr(getattr(router, "_incident", None), "spec_stats", None))
    for key, w in getattr(router.roles, "_wrapped", {}).items():
        print("llm", getattr(w, "name", key), getattr(w, "stats", None), "primary=", getattr(getattr(w, "primary", None), "model", None))
    h.teardown()


asyncio.run(main())
