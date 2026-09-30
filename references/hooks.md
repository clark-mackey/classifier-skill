# Hooks

Load when wiring the skill into Claude Code so it is offered without the model choosing it.

## Sub-agent nudge

`hooks/nudge_classifier.py` is a `PreToolUse` hook for the sub-agent tool. Domain skills often hand per-item judgment to a sub-agent (sort these PRs, bucket these search terms, score each checklist item), where this skill never gets the chance to trigger. The hook reads the sub-agent's prompt, and when plain code finds a judging verb whose object is the items ("classify these 40 terms", "score every ad", or "triage the open PRs:" introducing a list of 3 or more lines), it appends one note, marked `[classifier-nudge]`, suggesting the skill's batch mode.

- It calls no model and sends nothing anywhere; it only edits the prompt it is handed.
- It never blocks and never sets a permission decision. On any error it does nothing.
- A judging word used elsewhere does not count: "refactor the route handlers in these 4 files", "fix the filter bug" followed by steps, or "add a score column" stay silent, and so does a count under 3 ("score these 2 pages"). "Check" counts only with "each" or "every" or when it introduces a list, since "check the links" or "check the results" is usually a deterministic check.
- It stays silent when the prompt already names the classifier, marks a leaf worker that may not call other skills ("leaf worker", `MODEL_WORKER_LEAF`, "do not call any other skills" and similar), or when `CLASSIFIER_NUDGE=off`.
- The note tells the sub-agent to skip the classifier for open reasoning, writing, or local-only data without a local model, so it does not override this skill's data rules.

Wire it in `~/.claude/settings.json` (user-wide) or a project's `.claude/settings.json`, with the installed skill's absolute path:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Agent|Task",
        "hooks": [
          {"type": "command", "command": "python3 ~/.claude/skills/classifier-skill/hooks/nudge_classifier.py", "timeout": 5}
        ]
      }
    ]
  }
}
```

The sub-agent tool has been named both `Agent` and `Task`; the matcher covers both. Merge the entry into any existing `PreToolUse` list rather than replacing it.

## Measuring it

Every nudge leaves `[classifier-nudge]` in the sub-agent's transcript. Count nudges with `grep -rl "\[classifier-nudge\]" ~/.claude/projects/`, and compare with sessions that then ran `jev_decide.py`: a nudge that never leads to a call is either a false match (sharpen the hook's patterns) or a task the classifier should not take (leave it).
