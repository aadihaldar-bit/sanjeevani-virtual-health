"""Run scripted end-to-end conversations against the real router and LLMs.

    python e2e/run.py                         # every scenario
    python e2e/run.py incident_printer        # one or more by name
    python e2e/run.py --pace 3 --json out.json

Needs GROQ_API_KEY and OPENAI_API_KEY in the environment (backend/.env is loaded if present).
Nothing is sent to the real ticketing system.
"""
import argparse
import asyncio
import json
import logging
import os
import sys

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


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*")
    ap.add_argument("--pace", type=float, default=5.0, help="seconds the caller 'thinks' between turns")
    ap.add_argument("--json", default="")
    ap.add_argument("--quiet", action="store_true", help="hide transcripts")
    args = ap.parse_args()

    chosen = [s for s in SCENARIOS if not args.names or s.name in args.names]
    results = []
    failures = 0
    for sc in chosen:
        h = harness.Harness(pace_seconds=args.pace)
        await h.setup()
        try:
            session = await h.run(sc.workflow, sc.turns)
            checks = sc.check(session, h)
            if asyncio.iscoroutine(checks):
                checks = await checks
        finally:
            h.teardown()

        m = harness.metrics(session)
        print(f"\n{'=' * 100}\nSCENARIO {sc.name}  [{sc.workflow}]  - {sc.about}\n{'=' * 100}")
        if not args.quiet:
            for i, t in enumerate(session.turns, 1):
                print(f"\n  CALLER {i}: {t.user}\n  AGENT  {i}: {t.reply}\n      ({t.ms} ms{', tool=' + t.tool if t.tool else ''}{', node=' + str(t.node) if t.node else ''})")
        print("\n  checks:")
        for name, ok, detail in checks:
            print(f"    [{'PASS' if ok else 'FAIL'}] {name}" + (f"  -> {detail}" if detail and not ok else ""))
            failures += 0 if ok else 1
        print(f"  conversation: empathy_first={m['empathy_in_first_reply']} questions={m['questions_asked']} "
              f"solution_offered={m['solution_offered']} follow_up={m['follow_up_offered']} "
              f"avg_words={m['avg_reply_words']} p50={m['p50_ms']}ms max={m['max_ms']}ms")
        results.append({"scenario": sc.name, "metrics": m, "checks": [{"name": n, "pass": ok, "detail": d} for n, ok, d in checks],
                        "tickets": h.ticketing.tickets,
                        "transcript": [{"caller": t.user, "agent": t.reply, "ms": t.ms, "tool": t.tool} for t in session.turns]})

    total = sum(len(r["checks"]) for r in results)
    print(f"\n{'#' * 100}\nSUMMARY: {total - failures}/{total} checks passed across {len(results)} scenario(s)")
    for r in results:
        passed = sum(c["pass"] for c in r["checks"])
        m = r["metrics"]
        print(f"  {r['scenario']:22s} {passed}/{len(r['checks'])} checks | questions={m['questions_asked']} words/reply={m['avg_reply_words']} p50={m['p50_ms']}ms")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
