# PRD and build plan: calling the classifier from other skills

Status: **accepted design, not built.** Written 2026-09-26; revised twice the same day. Clark accepted the merged design (section 7) on 2026-09-26, after earlier noting the previous version was not good enough, simple enough, or durable enough. Building still starts only when he asks.

Reviews folded in:
1. code-owl architecture review (Claude Opus 5.5): client plus caller-owned specs.
2. GPT-6 Astra plan review: separate answers from permission, blind shadow, complete runs, contract 1.3.
3. GPT-6 Astra code-owl review of the install model: a separate stage is the default socket.
4. Claude Sonnet and GPT-6 Astra code-owl reviews of a harness interceptor: note only, replacement deferred.
5. GPT-6 Astra, given only the one-line goal, proposed a deep engine with a recipe interface; Claude Fable 5.1 reviewed it against this plan and produced the merged design adopted here, which cuts the first build roughly in half.

## 1. Problem

Several skills apply the same fixed-answer judgment to hundreds of items: search terms sorted into five actions, keywords tagged by intent, 1,500 name candidates filtered, candidate URLs tagged by site type. Today the premium LLM (Opus) does this item by item. That costs tokens in proportion to the item count, and judgment drifts over long lists.

classifier-skill already sends such judgments to TypeSafe Jev, a non-generative model that answers typed questions (`choice`, `noul`, `score`) with probabilities. But:

- The skill rarely triggers on its own inside other skills' workflows (usage analysis, 2026-09-25: missed opportunities, not bad results).
- The caller contract (`references/callers.md`, v1.2) leaves each caller to write its own lookup, batch handling, parsing, and fallback.
- Nobody has measured whether Jev is as accurate as Opus on these tasks. Wiring code-review to it did not help: open reasoning is a poor fit.
- Integration must scale: a user with 30 skills, 15 eligible, should not write custom code 15 times.
- Many skills update from someone else's repository, a plugin marketplace, or the claude.ai sync folder. Any line added to such a skill is erased on update.

## 2. Goals

1. Use the classifier wherever it genuinely helps, measured as coverage at an acceptable error rate, not as call count.
2. One deep engine with a small interface. The per-skill part is data (a question sheet), not code.
3. Third-party skills are never edited.
4. Accuracy is measured per sheet before the classifier's answers change anything.
5. Every run is auditable.
6. Dependencies point one way: callers depend on the classifier's contract; the classifier holds no caller's domain knowledge.

## 3. Non-goals

- Replacing Opus for open reasoning, writing, tool use, or one-off single-item judgment.
- Work that needs fetching or deep reading per item (code review, page audits).
- A workflow language in the sheet. Callers keep their own domain rules.
- A Python library entry point (callers are SKILL.md prose invoking a CLI).
- Replacing tool results in the harness (section 10).

## 4. When a task goes to the classifier

A step qualifies when a sheet exists for it and all of these hold:

- The answer set is fixed (`choice`, `noul`, or `score`).
- Each item can be judged from a short card of vetted facts, with no fetch and no deep read.
- A wrong answer is cheap to undo, or uncertain items go to a human.
- Either the item count is at least `min_items` (default 20, tuned by measurement), or the sheet is marked `recurring`: a fixed checkpoint that runs every time, where the point is consistency and an audit trail, not token savings. Building a request and reading the answer costs the LLM roughly 300-800 tokens, versus about 50 to judge one item inline, so single items never save tokens.

Below `min_items` on a non-recurring sheet, the LLM judges inline. That is expected, not a failure.

## 5. Callers

| Order | Caller skill | Step | How the list arrives | Volume |
|---|---|---|---|---|
| 1 | gta-account-optimization | task T13: classify every query with spend (T14 same shape) | Google Ads MCP result | ~400 a week |
| 2 | google-ads-tuning | wasted-spend sweep, keyword intent, employment filter | Google Ads MCP result | hundreds per account |
| 3 | name-generator | step 4 evaluation, 1,500+ down to 50-80 | the model generates it | ~1,500 |
| 4 | event-calendar-discovery | phase 2 site tagging, event-fit filter | search and fetch results | 20-80 |
| deferred | chatgpt-fanout, topical-map-generator | source tagging; keyword to cluster | tool results | wire only with volume data |

Each caller change needs the owner's go-ahead at the time. Coverage above comes from reading the callers' SKILL.md files, not from running them.

## 6. Data

All data and maps that existed on 2026-09-26, including client advertising data, may go to cloud LLM and AI services (owner's decision, 2026-09-26). Sheets for existing data use `data: cloud_ok`.

Still required:
- Secrets are redacted before sending (built, contract 1.2).
- Client names stay out of logs and learnings.
- A new data source, or anything marked local-only, needs a fresh decision; `data: local_only` makes the engine enforce `--local-only`.
- Only the sheet's card fields are sent, labeled as data. Nothing in a row can set a command, path, endpoint, or policy.
- Fallback judgment is done by the session that already holds the data.

## 7. Architecture (accepted)

### 7.1 One engine, small interface

```
classify_items.py --sheet SHEET.json --items ITEMS.jsonl --out OUT.jsonl --summary SUMMARY.json
```

Items and a sheet go in; stamped items and a summary come out. The engine hides everything else: building requests, redaction, size checks, provider choice, retries, validation, thresholds. It is built on `jev_decide.py`'s internals, so transport and validation exist once. The CLI contract of `jev_decide.py` stays.

### 7.2 Two knowledge layers

| Layer | Lives | Holds | Example |
|---|---|---|---|
| **Recipes** (generic) | inside classifier-skill, `recipes/<name>@<N>.json` | reusable question shapes with no caller's domain rules | `search-intent@1`, `card-sort@1`, `site-type@1` |
| **Sheets** (per use) | with the owner: the skill's folder for owned skills, or `~/.config/classifier-skill/sheets/` for third-party skills | a recipe reference or inline questions, plus the caller's options, card fields, thresholds, data rule | `gta-search-terms@1` |

The classifier never holds a caller's rules and keeps no caller registry, so a caller's change never forces a classifier release.

### 7.3 Uncertainty is normal

Every item ends in exactly one of three cases, and they are never merged:

| Case | Meaning | What happens |
|---|---|---|
| **No category fits** | the classifier answered "none fit" | routed to review |
| **Not enough evidence** | the classifier answered "insufficient context", or confidence is below the threshold, or the answer was flagged | routed to review |
| **Execution failed** | the classifier could not answer (reason code) | the LLM judges it, and the run reports it |

Plus the normal case: answered with confidence. An answer is evidence, never permission; the caller's rules decide actions.

### 7.4 Where to plug in (sockets)

Choose by one question: **where does the list first exist as data?**

| Rank | Socket | Use when | Edits to the skill |
|---|---|---|---|
| 1 | **Separate stage (default)**: fetch or receive the list, run the engine, hand the stamped list to the skill as ordinary input | the list exists, or can be fetched, before the skill judges it | none |
| 2 | **One line in the skill** | the owner authors the skill | one line |
| 3 | **Wrapper skill**: an owned skill runs the upstream skill and runs the engine at the step | a third-party skill whose list appears only mid-run | none to the upstream skill |

One optional sentence in global instructions may remind the model to use the classifier for repeated fixed-answer judgment. It is not relied on; coverage is measured from the call log.

### 7.5 Alternatives considered

| Option | Verdict |
|---|---|
| Each caller embeds lookup, batch, parse, and fallback prose | Rejected: duplicated, erased by third-party updates |
| Classifier holds caller recipes or a registry | Rejected: reverses the dependency |
| Global instruction as the main trigger | Rejected: prose triggers measurably miss |
| Harness module replacing list results | Deferred (section 10) |
| Overlay hook injecting sheets on skill load | Deferred: no hook input names the active skill |
| **One engine, generic recipes inside, sheets outside, ranked sockets (accepted)** | |

## 8. Component spec

### 8.1 Sheet

```json
{
  "sheet": "gta-search-terms",
  "version": 1,
  "contract": "1.3",
  "recipe": "search-action@1",
  "data": "cloud_ok",
  "min_items": 20,
  "recurring": false,
  "model": "typesafe/jev-1.13",
  "fields": {"id": "search_term", "card": ["search_term", "campaign", "ad_group", "match_type"]},
  "questions": {
    "action": {"type": "choice", "instructions": "...", "criteria": {"...": "..."}, "threshold": 0.8},
    "irrelevant": {"type": "noul", "instructions": "...", "criteria": {"true": "...", "false": "..."}, "threshold": 0.9}
  },
  "consumes": "T13 applies its five-action rules to `action`; `irrelevant` true supports a negative; review items are listed for the owner."
}
```

- `recipe` or `questions` (or both: questions override the recipe's).
- `fields` is the input mapping; only these fields are sent.
- `consumes` says in plain words how the step's existing rules use each answer. If they cannot without edits, the sheet says so and the skill needs socket 2 or 3.
- `choice` questions include "none fit" and "insufficient context" options where the task allows.
- Editing questions, fields, or the recipe reference means a new `version`.

### 8.2 Output line

```json
{"id": "term-0042", "status": "answered",
 "answers": {"action": {"choice": "exact_negative", "probabilities": {}, "confidence": 0.91}, "irrelevant": {"noul": 0.94}},
 "dispositions": {"action": "answered", "irrelevant": "answered"},
 "review": {},
 "versions": {"sheet": "gta-search-terms@1", "recipe": "search-action@1", "model": "typesafe/jev-1.13", "contract": "1.3"}}
```

- Items come out in input order, one line per input id.
- `status`: `answered`, or `unanswered` with a reason code.
- `dispositions`, per question: `answered`, `skip` (a `noul` confidently false), `human` (no fit, not enough evidence, or flagged).

### 8.3 Reason codes (execution failed)

`below_min_items` (expected bypass), `no_key`, `transport`, `refused_host`, `bad_request`, `too_large`, `invalid_answer`.

### 8.4 Summary file

Written last. Holds the versions, `items_in`, `items_out`, `complete`, answered count, review count per question, unanswered count per reason, cost, and seconds. A missing summary, `complete: false`, or `items_in` not equal to `items_out` means the caller uses the original list and judges it itself.

### 8.5 The `judge` procedure

Written once, in classifier-skill's SKILL.md: find the script (published lookup order), run it, check the summary, act on answered items under the caller's rules, list review items, judge failed items, and end with `Classifier: <answered>/<review>/<failed> (<reasons>)`. A socket only names the list and the sheet.

## 9. Accuracy: shadow and calibration

1. Every new sheet starts in **shadow**: the engine answers, the caller's LLM still decides.
2. The LLM's judgment is captured **blind**, in an isolated context from the same vetted facts, and recorded before any classifier output is shown. The two are joined by id afterward.
3. A labeled sample gets independent human labels (for gta, past decisions from change history), with outcome-revealing fields removed.
4. Thresholds are chosen on one split and reported on a held-out split (the `calibrate` recipe). A confidence of 0.9 is not 90% correct.
5. Report per sheet and per class: **coverage at the target error rate**, LLM tokens per 100 items, **cost per accepted judgment**, time.
6. Promotion is the caller's decision: shadow, then advisory (answers drive proposals; a human approves consequential actions), then auto only for reversible actions that clear the target. A new sheet, recipe, or model version goes back to shadow.

## 10. Harness hooks

Verified in Claude Code docs, 2026-09-26: a PostToolUse hook can replace a tool's result (`updatedToolOutput`; MCP output is not schema-checked); no hook input names the active skill; typing `/skillname` bypasses PreToolUse; matching hooks run in parallel; no Codex equivalent is documented.

- The existing sub-agent nudge stays.
- A note-only PostToolUse hook (adds "a sheet may apply", never replaces results) is deferred until two callers run.
- Replacing results stays deferred. It needs, first: a tested way to know which step's intent a tool call serves; one replacement owner per tool, tested with the token reducers; held-out accuracy per class and audits of hidden rows; a trace of every downstream use of the rows; and the original stored and retrievable.

## 11. Contract

**1.3**, additive. `jev_decide.py` and every 1.2 guarantee stay. 1.3 adds `classify_items.py` (lookup order: the same folders as `jev_decide.py`, plus `$CLASSIFIER_ITEMS_SCRIPT`), the sheet schema, recipe references, the output line, reason codes, and the summary file. Recipe, sheet, model, and contract versions are independent and recorded with every result.

## 12. Tests

Five core tests, with a stub transport, in `tests/test_scripts.py` (new class `ItemsEngine`):

1. every input id comes out exactly once, in order; duplicate ids exit 2
2. a transport failure mid-batch keeps the earlier answers and gives the rest a reason code, with correct summary counts
3. one invalid answer affects only that item
4. below `min_items` (non-recurring), nothing is sent
5. only the sheet's card fields are sent

Plus: dispositions follow thresholds; "none fit" and "insufficient context" route to `human`; versions appear on every line.

## 13. Build plan

| Phase | Work | Done when |
|---|---|---|
| 0 | Fix `jev_decide.py` batch partial failure: a transport error mid-batch exits 1 after printing earlier lines, with no stderr summary and no call-log entry (`run_batch`, the `call_jev` call in the loop) | regression test passes |
| 1 | `classify_items.py`, sheet schema, recipe references, `judge` in SKILL.md, contract 1.3, the tests in section 12; the gta T13 sheet; one separate stage that fetches T13's search terms and runs the engine, in shadow. Needs owner go-ahead for the gta repo | tests pass; code-owl review clean; installed copy synced; stage runs on real data |
| 2 | Two to three weeks of shadow runs with a blind LLM arm; calibrate against human labels; caller decides on advisory | per-class report; decision recorded |
| 3 | Second caller (google-ads-tuning). Rule of Three: move routing both callers repeat into the engine, and decide which deferred items (below) are now needed | second sheet in shadow; decisions written down |
| 4 | Remaining callers, each in shadow, then calibrated | per-sheet reports |

**Deferred until the second caller or a measured need:** source fingerprint and drift check (needed for wrappers and one-line sockets, not for a stage that fetches its own list); stored originals and `--get`; a whole-run deadline; parallel calls under the rate limit (needed before name-generator's 1,500 items); `--check-sheet`; the note-only hook; an onboarding tool that drafts sheets; the overlay hook; result replacement; resume from partial output.

## 14. Metrics

- Coverage at the target error rate, per sheet and per class.
- Cost per accepted judgment.
- LLM tokens per 100 judged items, before and after.
- Review rate and failure rate, with reason counts; any failed run is flagged, and failures over 10% of items are investigated.
- Calls per caller from the call log (how often the classifier is actually used).

## 15. Risks

| Risk | Mitigation |
|---|---|
| Calibration is tied to one model | every result records the model; a new model version returns the sheet to shadow |
| Silent fallback | summary file, reason counts, count line; usage-analyst checks failed runs |
| Shadow agreement mistaken for accuracy | blind arm recorded first, independent labels, held-out split |
| Answer treated as permission | dispositions never authorize; caller rules and status decide |
| Classifier absorbs caller knowledge | generic recipes only inside; sheets outside; no registry |
| Engine grows into a workflow engine | Rule of Three at phase 3; sheets hold questions, fields, and data rules only |
| Third-party update breaks a wrapper or one-line socket | drift check built before the first wrapper or one-line socket |
| Caller installed where classifier-skill is not | published lookup order; missing script means inline judgment, stated in the count line |
| Rate limit (1,200 requests a minute) | sequential calls stay under it; pacing before any parallel calls |

## 16. Open questions

1. Accuracy target per sheet and per class (owner, before phase 2 ends).
2. Source of human labels for gta T13, and how outcome-revealing fields are removed.
3. Whether the separate stage can fetch gta's search terms directly, or T13 needs a wrapper.
4. Whether the Google Ads skills also run under Codex or on other machines.
5. Jev cost per 1,000 items at these card sizes (measure in phase 1).
