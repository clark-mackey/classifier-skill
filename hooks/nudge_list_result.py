#!/usr/bin/env python3
"""Claude Code PostToolUse hook: when a tool result holds a long list, remind the main session that classifier-skill
can judge it.

A two-week transcript mine found that most missed classifier work starts the same way: a report comes back with
dozens of search terms, audit findings, or pages, and the agent (or the user) labels them one by one. The request
never named the per-item work, so the skill's description never matched. This hook looks at the result instead.
When plain code finds 20 or more rows that carry a text field someone would judge (a search term, keyword, query,
finding, headline) in a JSON list of objects or a tab/pipe table with a header, it adds one note, marked
"[classifier-nudge]", to the model's context. It calls no model, never blocks, fires at most once per tool per
session, and on any error does nothing (exit 0, no output).

Skipped for the classifier's own output, for leaf workers (MODEL_WORKER_LEAF=1), and when CLASSIFIER_NUDGE=off.
Wire it as a PostToolUse hook; see references/hooks.md."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile

MARKER = "[classifier-nudge]"
MIN_ROWS = 20
NOTE = (f"{MARKER} This result holds {{rows}} rows. If you will judge each row the same way with a fixed answer set "
        "(intent, keep or negate, route, triage, tag, score), use the classifier-skill skill's batch mode for those "
        "judgments instead of making them one by one. Skip it for facts code can compute (counts, thresholds, "
        "status), for open reasoning or writing, and for data that must stay on this machine when no local model is "
        "configured.")
OWN = re.compile(r"jev_decide|classify_items|classifier-skill|t13\.py|model-worker jev", re.I)
TABLE_LINE = re.compile(r"\t|^\s*\|.*\|\s*$")
# A row is worth a judgment when it carries text such as a search term, keyword, query, anchor, finding, or headline.
# Rows of only metrics, dates, IDs, statuses, or URLs (site lists, metric histories, tool listings) never count.
ITEM_FIELD = re.compile(r"search_?term|keyword|query|anchor|issue|finding|headline|description|title|review|"
                        r"comment|subject|snippet|message", re.I)
# Edit results carry patch line lists and sub-agent results are prose about work already done; neither is a list to
# judge. Read counts only for data files, since source code has tabs and long arrays of its own.
SKIP_TOOLS = re.compile(r"^(?:Edit|MultiEdit|Write|NotebookEdit|Agent|Task|TodoWrite|Skill|Glob|Grep)$")
DATA_FILE = re.compile(r"\.(?:csv|tsv|jsonl|json)$", re.I)


def texts(value):
    """Every string in a tool response, whatever its shape (string, content blocks, or nested JSON)."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from texts(v)
    elif isinstance(value, list):
        for v in value:
            yield from texts(v)


def keys(item):
    """Field names of one row, nested objects included (GAQL rows nest: searchTermView.searchTerm)."""
    if isinstance(item, dict):
        for k, v in item.items():
            yield str(k)
            yield from keys(v)


def item_lists(value):
    """Every list of objects whose rows name a text field someone would judge item by item."""
    if isinstance(value, list):
        if len(value) >= MIN_ROWS and isinstance(value[0], dict) and any(ITEM_FIELD.search(k) for k in keys(value[0])):
            yield len(value)
        for v in value:
            yield from item_lists(v)
    elif isinstance(value, dict):
        for v in value.values():
            yield from item_lists(v)


def table_rows(text: str) -> int:
    """Rows of a tab or pipe table whose header names a judged field; 0 for any other text."""
    lines = [line for line in text.splitlines() if TABLE_LINE.search(line)]
    return len(lines) - 1 if lines and ITEM_FIELD.search(lines[0]) else 0


def rows(response) -> int:
    """Rows in the longest judgeable list: JSON lists of objects (in the response or in JSON text inside it), or a
    table with a header row. Lists of metrics, dates, IDs, or tool names do not count."""
    best = max(item_lists(response), default=0) if not isinstance(response, str) else 0
    for text in texts(response):
        stripped = text.strip()
        if stripped[:1] in "[{":
            try:
                best = max([best, *item_lists(json.loads(stripped))])
                continue
            except ValueError:
                pass
        best = max(best, table_rows(text))
    return best


def first_time(session: str, tool: str) -> bool:
    """True once per (session, tool): a marker file in the temp dir records that the note was shown."""
    key = hashlib.sha256(f"{session}\0{tool}".encode()).hexdigest()[:24]
    path = os.path.join(tempfile.gettempdir(), f"classifier-nudge-{key}")
    try:
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        return True
    except FileExistsError:
        return False


def main() -> None:
    if (os.environ.get("CLASSIFIER_NUDGE", "").strip().lower() == "off"
            or os.environ.get("MODEL_WORKER_LEAF", "").strip() == "1"):
        return
    event = json.load(sys.stdin)
    tool = str(event.get("tool_name") or "")
    tool_input = event.get("tool_input") or {}
    if not tool or SKIP_TOOLS.match(tool) or OWN.search(json.dumps(tool_input)):
        return
    if tool == "Read" and not DATA_FILE.search(str(tool_input.get("file_path") or "")):
        return
    count = rows(event.get("tool_response"))
    if count < MIN_ROWS or not first_time(str(event.get("session_id") or ""), tool):
        return
    json.dump({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                      "additionalContext": NOTE.format(rows=count)}}, sys.stdout)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # fail open: a broken nudge must never disturb the session
        pass
