#!/usr/bin/env python3
"""Wire nudge_list_result.py into ~/.claude/settings.json as a PostToolUse hook.

Usage: python3 install_list_nudge.py [--settings PATH]

Safe to re-run: when an entry already runs nudge_list_result.py it changes nothing. Before writing, it copies the
settings file to settings.json.bak-<timestamp> next to it."""

import argparse
import json
import shutil
import time
from pathlib import Path

COMMAND = "python3 ~/.claude/skills/classifier-skill/hooks/nudge_list_result.py"
ENTRY = {"matcher": "mcp__.*|Bash|Read|WebFetch", "hooks": [{"type": "command", "command": COMMAND, "timeout": 5}]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--settings", type=Path, default=Path.home() / ".claude/settings.json")
    path = parser.parse_args().settings
    settings = json.loads(path.read_text()) if path.exists() else {}
    post = settings.setdefault("hooks", {}).setdefault("PostToolUse", [])
    if any("nudge_list_result.py" in h.get("command", "") for e in post for h in e.get("hooks", [])):
        print(f"already wired in {path}")
        return 0
    if path.exists():
        backup = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(path, backup)
        print(f"backup: {backup}")
    post.append(ENTRY)
    path.write_text(json.dumps(settings, indent=2) + "\n")
    print(f"added PostToolUse list nudge to {path}; restart Claude Code sessions to load it")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
