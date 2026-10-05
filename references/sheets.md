# Judge a list with a sheet

Load when a question sheet exists for the task: a caller's, or one kept in `~/.config/classifier-skill/sheets/`. Run `classify_items.py` instead of writing the batch yourself.

1. Write the items to a JSONL file created with `mktemp`, one object per line, with the sheet's id and card fields.
2. Run `python3 <skill-directory>/scripts/classify_items.py --sheet <sheet> --items <file> --out <out> --summary <summary> --caller <calling skill>`. Add `--dry-run` to see every payload without sending, whatever the item count.
3. Read the summary. If it is missing, `complete` is false, or `items_out` differs from `items_in`, judge the original list yourself and label those judgments as yours.
4. Apply the calling step's own rules to `answered` dispositions, list `human` ones for review, and judge `unanswered` items yourself, labelled as yours. Items with `below_min_items` are expected: a sheet's `min_items` (default 20) is the caller's threshold and replaces the 3-item rule in SKILL.md. Any other reason is a failure to report.
5. End with the `Classifier: <answered>/<human>/<unanswered> (<reasons>)` line the script prints.

This is the one path that hands items back to the working model; everywhere else a failed call is reported, never replaced by your own answers.

Sheets hold data only: questions or a generic recipe from `recipes/`, the card fields, thresholds, and the data rule. The schema, output lines, and reason codes are in [callers.md](callers.md). An answer is evidence, never permission to act.
