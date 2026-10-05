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


def number(value) -> float:
    """A log value as a number; older or odd records (a string cost, null) count as 0 rather than crashing the report."""
    try:
        value = float(value or 0)
    except (TypeError, ValueError):
        return 0
    return int(value) if value.is_integer() else value


def load(path: Path, since: str | None) -> list[dict]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and (not since or str(record.get("ts", "")) >= since):
            records.append(record)
    return records


def group(records: list[dict], field: str, missing: str) -> dict[str, dict]:
    """Per-value totals for one log field, largest item count first."""
    rows: dict[str, Counter] = defaultdict(Counter)
    for r in records:
        row = rows[r.get(field) or missing]
        row["calls"] += 1
        row["failed"] += bool(r.get("failed"))
        for key in ("items", "flagged", "invalid", "input_tokens"):
            row[key] += number(r.get(key))
        row["cost"] += number(r.get("cost"))
        row["questions"] += len(r.get("questions") or {})
    return {k: {**v, "cost": round(v["cost"], 6),
                "flag_rate": round(v["flagged"] / v["items"], 3) if v["items"] else None,
                "questions_per_call": round(v["questions"] / v["calls"], 1)}
            for k, v in sorted(rows.items(), key=lambda kv: -kv[1]["items"])}


def summarize(records: list[dict]) -> dict:
    types, tasks, flags, review = Counter(), Counter(), Counter(), Counter()
    for r in records:
        types.update((r.get("questions") or {}).values())
        flags.update(r.get("flags_by_question") or {})
        review.update(r.get("human_by_question") or {})
        if r.get("task"):
            tasks[r["task"]] += 1
    calls = len(records)
    items = sum(number(r.get("items")) for r in records)
    stamps = sorted(str(r["ts"]) for r in records if r.get("ts"))
    return {
        "calls": calls,
        "first": stamps[0] if stamps else None,
        "last": stamps[-1] if stamps else None,
        "items": items,
        "reshape_noted": sum(bool(r.get("reshape_noted")) for r in records),
        "failed": sum(bool(r.get("failed")) for r in records),
        "flag_rate": round(sum(number(r.get("flagged")) for r in records) / items, 3) if items else None,
        "invalid": sum(number(r.get("invalid")) for r in records),
        "cost": round(sum(number(r.get("cost")) for r in records), 6),
        "question_types": dict(types),
        "flags_by_question": dict(flags.most_common()),
        "review_by_question": dict(review.most_common()),
        "by_recipe": group(records, "recipe", "(custom)"),
        "by_caller": group(records, "caller", "(direct)"),
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
    print(f"{summary['calls']} calls, {summary['items']:g} items, {summary['reshape_noted']} with a reshape note, "
          f"flag rate {summary['flag_rate']}, {summary['invalid']:g} invalid, {summary['failed']} failed, "
          f"cost ${summary['cost']}")
    print(f"log covers {summary['first']} to {summary['last']}")
    print(f"question types: {summary['question_types']}")
    if summary["flags_by_question"]:
        print(f"flags per question (batch calls): {summary['flags_by_question']}")
    if summary["review_by_question"]:
        print(f"sent to a person per question (sheet calls): {summary['review_by_question']}")
    for label, rows in (("recipe", summary["by_recipe"]), ("caller", summary["by_caller"])):
        print(f"{label:<22}{'calls':>6}{'failed':>7}{'items':>7}{'q/call':>8}{'flagged':>9}{'rate':>7}"
              f"{'invalid':>8}{'cost':>11}")
        for name, row in rows.items():
            print(f"{name:<22}{row['calls']:>6}{row['failed']:>7}{row['items']:>7g}{row['questions_per_call']:>8}"
                  f"{row['flagged']:>9g}{str(row['flag_rate']):>7}{row['invalid']:>8g}{row['cost']:>11.6f}")
    if summary["top_tasks"]:
        print("most frequent reshaped tasks:")
        for task, count in summary["top_tasks"]:
            print(f"  {count:>3}  {task}")


if __name__ == "__main__":
    main()
