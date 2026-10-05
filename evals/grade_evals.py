#!/usr/bin/env python3
"""Mechanically grade classifier-skill eval runs from Codex --json events. Usage: python3 evals/grade_evals.py ITERATION"""
import json
import os
import re
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
# A command that runs a classifier script, however it is launched (python3 -u, ./script, or a subprocess call
# inside python3 -c or a heredoc); not one that reads its source or only asks for help or the version
SCRIPT_RUN = re.compile(r"(?:jev_decide|classify_items)\.py")
READERS = {"sed", "rg", "grep", "cat", "head", "tail", "nl", "wc", "less", "rtk", "ls", "awk"}
WRAPPER = re.compile(r"""^\s*(?:/bin/)?(?:z|ba)?sh\s+-l?c\s+['"]?""")


def unwrap(command):
    """The command as the shell ran it: no `zsh -lc '...'` wrapper, and no backslash-escaped quotes."""
    return WRAPPER.sub("", command).replace('\\"', '"')


def reads_source(command):
    """True when the command only reads a script (sed, rg, cat, ...) rather than running it."""
    first = unwrap(command).split(None, 1)
    return bool(first) and first[0].rsplit("/", 1)[-1] in READERS and bool(SCRIPT_RUN.search(command))
INFO_ONLY = ("--help", "--contract-version")


def json_lines(path):
    """Parsed JSON lines; a line cut off when a timed-out run was killed is skipped, not fatal."""
    if not path.exists():
        return []
    records = []
    for line in path.read_text().splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return [r for r in records if isinstance(r, dict)]


def call_log(run):
    """Reshape evidence from the run's own call log (metadata only)."""
    path = run / "tmp/calls.jsonl"
    records = json_lines(path)
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
    for event in json_lines(run / "events.jsonl"):
        item = event.get("item") or {}
        if item.get("type") == "command_execution":
            commands.append(item.get("command", ""))
        elif item.get("type") == "agent_message":
            messages.append(item.get("text", ""))
    final = (run / "final.txt").read_text() if (run / "final.txt").exists() else ""
    text = "\n".join(messages + [final])
    admissible = re.findall(r"Admissible:[^\n]*", text)
    commands = [unwrap(c) for c in commands]
    jev_calls = [c for c in commands if SCRIPT_RUN.search(c) and not reads_source(c)
                 and not any(flag in c for flag in INFO_ONLY)]
    log = call_log(run)
    return {
        "skill_read": any("classifier-skill" in c or "skills/jev-openrouter" in c for c in commands),  # old name
        # the call log is written only by calls that reached the provider, so it is the stronger evidence
        "jev_live_call": log["log_calls"] > 0 or any("--dry-run" not in c for c in jev_calls),
        "jev_dry_run": any("--dry-run" in c for c in jev_calls),
        "recipes_read": any("recipes.md" in c for c in commands),
        "batch_used": any("--batch" in c or "classify_items.py" in c for c in jev_calls),
        "criteria_seen": sorted({k for k in ("same_intent_duplicate", "partial_overlap", "insufficient_context", "none_fit",
                                             "no_evidence", "contradiction") if any(k in c for c in commands)}),
        # requests are often written to a file in one command and sent with --request-file in another
        "types_seen": sorted({t for t in ("noul", "choice", "score")
                              if any(re.search(rf'"type"\s*:\s*"{t}"', c) for c in commands)}
                             | set(log["log_question_types"])),
        "script_source_read": any(reads_source(c) for c in commands),
        "wrote_request_file": any(("apply_patch" in c or "> " in c) and "request" in c.lower() and ".json" in c
                                  for c in commands),
        "admissible_line": admissible[0][:200] if admissible else None,
        "reshape_block": bool(re.search(r"^\s*\**Reshape:", text, re.M)),
        **log,
        "commands": len(commands),
        "seconds": json.loads((run / "timing.json").read_text())["seconds"],
        "final_excerpt": final.strip()[:300],
    }


def main():
    root = (Path(os.environ.get("CLASSIFIER_EVAL_WORKSPACE", SKILL.parent / "classifier-skill-workspace"))
            / f"iteration-{sys.argv[1]}")
    report = {}
    for run in sorted(root.glob("**/eval-*/*/run-*")):
        name = str(run.relative_to(root))
        report[name] = facts(run)
        (run / "grading.json").write_text(json.dumps(report[name], indent=2))
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
