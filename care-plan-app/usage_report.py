"""Adds up the token log (data/llm_usage.jsonl): tokens and estimated cost per day, feature and model.

    python usage_report.py [--file data/llm_usage.jsonl] [--since 2026-11-04] [--inr-rate 95.2]

PRICES are USD per 1M tokens (standard on-demand, text/image input) from Google's Cloud Billing catalog on 2026-11-04; a model that
is not listed shows its tokens and no cost. Thinking tokens are billed as output. Cached input is counted at the full input price, so
the estimate can only run a little high. The Cloud Billing report is the authority on what was charged.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional

PRICES = {  # model: (input, output) USD per 1M tokens
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3.5-flash": (1.50, 9.00),
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-2.5-flash": (0.30, 2.50),
}
IST = timedelta(hours=5, minutes=30)  # the team's day, not UTC


def cost(model: str, tokens_in: int, tokens_out: int) -> Optional[float]:
    price = PRICES.get(model)
    return None if price is None else (tokens_in * price[0] + tokens_out * price[1]) / 1e6


def load(path: str, since: Optional[str] = None) -> List[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except ValueError:
                continue  # a half-written last line
            row["day"] = (datetime.strptime(row["ts"], "%Y-%m-%dT%H:%M:%SZ") + IST).strftime("%Y-%m-%d")
            if not since or row["day"] >= since:
                rows.append(row)
    return rows


def summarise(rows: List[dict], key: str) -> Dict[str, dict]:
    """Totals per value of `key` (day, purpose or model). `out` already includes thinking tokens."""
    out: Dict[str, dict] = defaultdict(lambda: {"calls": 0, "in": 0, "out": 0, "thinking": 0, "usd": 0.0, "priced": True})
    for r in rows:
        t = out[r[key]]
        billed_out = r["output"] + r["thoughts"]
        t["calls"] += 1
        t["in"] += r["input"]
        t["out"] += billed_out
        t["thinking"] += r["thoughts"]
        c = cost(r["model"], r["input"], billed_out)
        if c is None:
            t["priced"] = False
        else:
            t["usd"] += c
    return dict(sorted(out.items()))


def _print(title: str, table: Dict[str, dict], inr_rate: Optional[float]) -> None:
    print(f"\n{title}")
    print(f"  {'':<24}{'calls':>6}{'input':>11}{'output':>11}{'(thinking)':>12}{'USD':>9}" + (f"{'INR':>9}" if inr_rate else ""))
    for name, t in table.items():
        usd = f"{t['usd']:.4f}" + ("" if t["priced"] else "+")
        print(f"  {name:<24}{t['calls']:>6}{t['in']:>11,}{t['out']:>11,}{t['thinking']:>12,}{usd:>9}"
              + (f"{t['usd'] * inr_rate:>9.2f}" if inr_rate else ""))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--file", default=os.getenv("LLM_USAGE_LOG", os.path.join("data", "llm_usage.jsonl")))
    ap.add_argument("--since", help="first day to include, YYYY-MM-DD (IST)")
    ap.add_argument("--inr-rate", type=float, help="rupees per US dollar, to show a rupee column")
    args = ap.parse_args()
    rows = load(args.file, args.since) if os.path.exists(args.file) else []
    if not rows:
        print("no calls recorded yet")
        return
    for title, key in (("By day (IST)", "day"), ("By feature", "purpose"), ("By model", "model")):
        _print(title, summarise(rows, key), args.inr_rate)
    total = summarise([{**r, "all": "all"} for r in rows], "all")
    _print("Total", total, args.inr_rate)
    if not all(t["priced"] for t in total.values()):
        print("\n  + = some calls used a model with no price in PRICES, so the cost is a lower bound")


if __name__ == "__main__":
    main()
