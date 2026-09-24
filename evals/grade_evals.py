#!/usr/bin/env python3
"""Mechanically grade classifier-skill eval runs from Codex --json events. Usage: python3 evals/grade_evals.py ITERATION"""
import json
import os
import re
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
root = Path(os.environ.get("CLASSIFIER_EVAL_WORKSPACE", SKILL.parent / "classifier-skill-workspace")) / f"iteration-{sys.argv[1]}"


def call_log(run):
    """Reshape evidence from the run's own call log (metadata only)."""
    path = run / "tmp/calls.jsonl"
    records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []
    return {
        "log_calls": len(records),
        "log_reshape_noted": sum(bool(r.get("reshape_noted")) for r in records),
        "log_recipes": sorted({r.get("recipe") or "(custom)" for r in records}),
        "log_items": sum(r.get("items") or 0 for r in records),
        "log_question_types": sorted({t for r in records for t in (r.get("questions") or {}).values()}),
        "log_flagged": sum(r.get("flagged") or 0 for r in records),
    }


def facts(run):
    commands, messages = [], []
    for line in (run / "events.jsonl").read_text().splitlines():
        item = json.loads(line).get("item", {}) if line.startswith("{") else {}
        if item.get("type") == "command_execution":
            commands.append(item.get("command", ""))
        elif item.get("type") == "agent_message":
            messages.append(item.get("text", ""))
    final = (run / "final.txt").read_text() if (run / "final.txt").exists() else ""
    text = "\n".join(messages + [final])
    admissible = re.findall(r"Admissible:[^\n]*", text)
    jev_calls = [c for c in commands if "jev_decide.py" in c]
    return {
        "skill_read": any("classifier-skill" in c for c in commands),
        "jev_live_call": any("--dry-run" not in c for c in jev_calls),
        "jev_dry_run": any("--dry-run" in c for c in jev_calls),
        "recipes_read": any("recipes.md" in c for c in commands),
        "batch_used": any("--batch" in c for c in jev_calls),
        "criteria_seen": sorted({k for k in ("same_intent_duplicate", "partial_overlap", "insufficient_context", "none_fit",
                                             "no_evidence", "contradiction") if any(k in c for c in jev_calls)}),
        "types_seen": sorted({t for t in ("noul", "choice", "score")
                              if any(re.search(rf'"type"\s*:\s*"{t}"', c) for c in jev_calls)}),
        "script_source_read": any(re.search(r"(sed -n|rg |cat |rtk read |head |tail )[^|;]*jev_decide\.py", c)
                                  for c in commands),
        "wrote_request_file": any(("apply_patch" in c or "> " in c) and "request" in c.lower() and ".json" in c
                                  for c in commands),
        "admissible_line": admissible[0][:200] if admissible else None,
        "reshape_block": bool(re.search(r"^\s*\**Reshape:", text, re.M)),
        **call_log(run),
        "commands": len(commands),
        "seconds": json.loads((run / "timing.json").read_text())["seconds"],
        "final_excerpt": final.strip()[:300],
    }


report = {}
for run in sorted(root.glob("**/eval-*/*/run-*")):
    name = str(run.relative_to(root))
    report[name] = facts(run)
    (run / "grading.json").write_text(json.dumps(report[name], indent=2))
print(json.dumps(report, indent=1))
