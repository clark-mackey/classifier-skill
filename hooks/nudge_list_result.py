#!/usr/bin/env python3
"""Claude Code PostToolUse hook: when a tool result holds a long list, remind the main session that classifier-skill
can judge it.

A two-week transcript mine found that most missed classifier work starts the same way: a report comes back with
dozens of search terms, audit findings, or pages, and the agent (or the user) labels them one by one. The request
never named the per-item work, so the skill's description never matched. This hook looks at the result instead.
When plain code finds 20 or more rows that carry a text field someone would judge (a search term, keyword, query,
finding, headline) in a JSON list of objects, JSON Lines, or a tab, pipe, or comma table with a header, it adds one note, marked
"[classifier-nudge]", to the model's context. It calls no model, never blocks, fires at most once per tool per
session, and on any error does nothing (exit 0, no output).

Skipped for the classifier's own output, for leaf workers (MODEL_WORKER_LEAF=1), and when CLASSIFIER_NUDGE=off.
Wire it as a PostToolUse hook; see references/hooks.md."""

from __future__ import annotations

import csv
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
SEPARATOR = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")  # a markdown table's |---|---| line is not a row
# A row is worth a judgment when it carries text such as a search term, keyword, query, anchor, finding, or headline.
# A field counts when its name ends in one of these nouns, optionally followed by "text" ("searchTerm", "keyword",
# "Anchor text"); "reviewCount" or "titleLength" are metrics about the text and do not count. Rows of only metrics,
# dates, IDs, statuses, or URLs (site lists, metric histories, tool listings) never count.
NOUNS = {"term", "terms", "keyword", "keywords", "query", "queries", "anchor", "issue", "finding", "headline",
         "review", "comment", "subject", "snippet"}
WORD = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])")
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


def judged(name: str) -> bool:
    words = [w.lower() for w in WORD.findall(name)]
    if words[-1:] == ["text"]:
        words = words[:-1]
    return bool(words) and words[-1] in NOUNS


def keys(item):
    """Field names of one row, nested objects included (GAQL rows nest: searchTermView.searchTerm)."""
    if isinstance(item, dict):
        for k, v in item.items():
            yield str(k)
            yield from keys(v)


def item_lists(value):
    """Every list of objects whose rows name a text field someone would judge item by item."""
    if isinstance(value, list):
        if len(value) >= MIN_ROWS:  # count matching rows, so a leading totals or summary object does not hide them
            yield sum(1 for v in value if isinstance(v, dict) and any(judged(k) for k in keys(v)))
        for v in value:
            yield from item_lists(v)
    elif isinstance(value, dict):
        for v in value.values():
            yield from item_lists(v)


def table_rows(text: str) -> int:
    """Rows of a tab, pipe, or comma table whose header names a judged field; 0 for any other text."""
    lines = [line for line in text.splitlines() if TABLE_LINE.search(line) and not SEPARATOR.match(line)]
    if lines:
        header = re.split(r"\t|\|", lines[0])
        return len(lines) - 1 if any(judged(cell) for cell in header) else 0
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) <= MIN_ROWS or "," not in lines[0]:
        return 0
    parsed = list(csv.reader(lines))
    width = len(parsed[0])
    return sum(1 for row in parsed[1:] if len(row) == width) if any(judged(cell) for cell in parsed[0]) else 0


def json_rows(text: str):
    """The parsed JSON in a text, a JSON Lines text as a list of its records, or None for anything else."""
    try:
        return json.loads(text)
    except ValueError:
        pass
    try:
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    except ValueError:
        return None


def rows(response) -> int:
    """Rows in the longest judgeable list: JSON lists of objects (in the response or in JSON text inside it), or a
    table with a header row (tab, pipe, or comma), or JSON Lines. Lists of metrics, dates, IDs, or tool names do not count."""
    best = max(item_lists(response), default=0) if not isinstance(response, str) else 0
    for text in texts(response):
        stripped = text.strip()
        parsed = json_rows(stripped) if stripped[:1] in "[{" else None
        if parsed is not None:
            best = max([best, *item_lists(parsed)])
        else:
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
    if count < MIN_ROWS:
        return
    # Without a session id, the parent process (the harness) stands in, so the marker never outlives the session.
    session = str(event.get("session_id") or f"ppid-{os.getppid()}")
    payload = json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                                 "additionalContext": NOTE.format(rows=count)}})
    if first_time(session, tool):
        sys.stdout.write(payload)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # fail open: a broken nudge must never disturb the session
        pass
