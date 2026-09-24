#!/usr/bin/env python3
"""Summarize the classifier-skill call log so reshaped work can be reviewed and the skill re-tuned.

Reads the JSONL log that jev_decide.py appends to (CLASSIFIER_SKILL_LOG, default
~/.local/state/classifier-skill/calls.jsonl). The log holds metadata only, never state or answers.
Usage: reshape_report.py [--log PATH] [--since YYYY-MM-DD] [--json]"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path


def default_log() -> Path:
    configured = os.environ.get("CLASSIFIER_SKILL_LOG", "").strip()
    if configured and configured.lower() != "off":
        return Path(configured).expanduser()
    state_home = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    return state_home / "classifier-skill/calls.jsonl"


def load(path: Path, since: str | None) -> list[dict]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not since or record.get("ts", "") >= since:
            records.append(record)
    return records


def summarize(records: list[dict]) -> dict:
    by_recipe: dict[str, dict] = defaultdict(lambda: Counter())
    types, tasks = Counter(), Counter()
    for r in records:
        recipe = r.get("recipe") or "(custom)"
        row = by_recipe[recipe]
        row["calls"] += 1
        for key in ("items", "flagged", "invalid", "input_tokens"):
            row[key] += r.get(key) or 0
        row["cost"] += r.get("cost") or 0
        row["questions"] += len(r.get("questions") or {})
        types.update((r.get("questions") or {}).values())
        if r.get("task"):
            tasks[r["task"]] += 1
    calls = len(records)
    items = sum(r.get("items") or 0 for r in records)
    return {
        "calls": calls,
        "items": items,
        "reshape_noted": sum(bool(r.get("reshape_noted")) for r in records),
        "flag_rate": round(sum(r.get("flagged") or 0 for r in records) / items, 3) if items else None,
        "invalid": sum(r.get("invalid") or 0 for r in records),
        "cost": round(sum(r.get("cost") or 0 for r in records), 6),
        "question_types": dict(types),
        "by_recipe": {k: {**v, "cost": round(v["cost"], 6),
                          "flag_rate": round(v["flagged"] / v["items"], 3) if v["items"] else None,
                          "questions_per_call": round(v["questions"] / v["calls"], 1)}
                      for k, v in sorted(by_recipe.items(), key=lambda kv: -kv[1]["items"])},
        "top_tasks": tasks.most_common(10),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, default=None)
    parser.add_argument("--since", help="only records on or after this ISO date")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    path = args.log or default_log()
    if not path.exists():
        print(f"no call log at {path}", file=sys.stderr)
        raise SystemExit(1)
    summary = summarize(load(path, args.since))
    if args.json:
        json.dump(summary, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return
    print(f"{summary['calls']} calls, {summary['items']} items, {summary['reshape_noted']} with a reshape note, "
          f"flag rate {summary['flag_rate']}, {summary['invalid']} invalid, cost ${summary['cost']}")
    print(f"question types: {summary['question_types']}")
    print(f"{'recipe':<22}{'calls':>6}{'items':>7}{'q/call':>8}{'flagged':>9}{'rate':>7}{'cost':>11}")
    for recipe, row in summary["by_recipe"].items():
        print(f"{recipe:<22}{row['calls']:>6}{row['items']:>7}{row['questions_per_call']:>8}"
              f"{row['flagged']:>9}{str(row['flag_rate']):>7}{row['cost']:>11.6f}")
    if summary["top_tasks"]:
        print("most frequent reshaped tasks:")
        for task, count in summary["top_tasks"]:
            print(f"  {count:>3}  {task}")


if __name__ == "__main__":
    main()
