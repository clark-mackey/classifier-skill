#!/usr/bin/env python3
"""Claude Code PreToolUse hook: nudge a sub-agent toward classifier-skill when its task judges many items.

Domain skills (PR sweeps, ads negatives, checklists) often hand per-item judgment to a sub-agent, where
classifier-skill never gets the chance to trigger. This hook reads the sub-agent's prompt, and when plain code
finds a judging verb plus a list of 3 or more items, it appends one marked note ("[classifier-nudge]") to the
prompt. It calls no model, never blocks, and on any error does nothing (exit 0, no output).

Skipped when the prompt already names the classifier, marks a leaf worker that may not call other skills, or
CLASSIFIER_NUDGE=off. Wire it with matcher "Agent|Task"; see references/hooks.md.
Idea adapted from jev-kit's PreToolUse guard (MIT, github.com/jonathanavis96/jev-kit); code written fresh."""

from __future__ import annotations

import json
import os
import re
import sys

MARKER = "[classifier-nudge]"
NOTE = (f"\n\n{MARKER} This task judges many items the same way. If each judgment has a fixed answer set "
        "(tag, sort, filter, route, triage, score, rank, dedupe), load the classifier-skill skill and use its batch "
        "mode for the per-item calls instead of judging every item yourself. Skip this when the items need open "
        "reasoning or writing, or when the data must stay on this machine and no local model is configured.")
VERBS = re.compile(r"\b(classif\w*|categori[sz]\w*|tag(?:s|ged|ging)?|triag\w*|sort(?:s|ed|ing)?|bucket\w*|"
                   r"scor(?:e|es|ed|ing)|rank(?:s|ed|ing)?|dedup\w*|filter(?:s|ed|ing)?|rout(?:e|es|ed|ing)|"
                   r"label(?:s|ed|led|ing|ling)?|grad(?:e|es|ed|ing)|tier(?:s|ed)?)\b", re.I)
NOUNS = (r"items|keywords|terms|queries|pages|urls|links|tickets|emails|messages|prs|pull requests|issues|posts|"
         r"ads|leads|rows|records|files|reviews|comments|cards|results|domains|products|findings|candidates")
COUNT = re.compile(rf"\b(\d{{1,6}})\s+(?:\w+\s+)?(?:{NOUNS})\b", re.I)
SINGULAR = (r"item|keyword|term|query|page|url|link|ticket|email|message|pr|pull request|issue|post|ad|lead|row|"
            r"record|file|review|comment|card|result|domain|product|finding|candidate")
EACH = re.compile(rf"\b(?:each|every)\s+(?:of\s+the\s+)?(?:\w+\s+)?(?:{SINGULAR}|{NOUNS})\b", re.I)
LIST_LINE = re.compile(r"^\s*(?:\d{1,4}[.)]|[-*•])\s+\S", re.M)
SKIP = re.compile(r"classifier-skill|jev_decide|\bjev\b|typesafe|leaf worker|MODEL_WORKER_LEAF|"
                  r"do not call other (?:models|skills)", re.I)


def wants_nudge(prompt: str) -> bool:
    if not prompt or MARKER in prompt or SKIP.search(prompt) or not VERBS.search(prompt):
        return False
    many = any(int(m.group(1)) >= 3 for m in COUNT.finditer(prompt))
    return many or bool(EACH.search(prompt)) or len(LIST_LINE.findall(prompt)) >= 3


def main() -> None:
    if os.environ.get("CLASSIFIER_NUDGE", "").strip().lower() == "off":
        return
    event = json.load(sys.stdin)
    tool_input = event.get("tool_input") or {}
    if event.get("tool_name") not in ("Agent", "Task") or not wants_nudge(str(tool_input.get("prompt") or "")):
        return
    updated = {**tool_input, "prompt": tool_input["prompt"] + NOTE}
    json.dump({"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": updated}}, sys.stdout)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # fail open: a broken nudge must never stop a sub-agent from starting
        pass
