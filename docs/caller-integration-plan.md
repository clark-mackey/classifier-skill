# PRD and build plan: calling the classifier from other skills

Status: draft design, not built. Written 2026-09-26, revised 2026-09-26 (install model and harness review).

> **Owner's note (2026-09-26):** Clark is still not convinced this approach is good enough, simple enough, or durable enough. Treat the design as unapproved; do not start phase 1 until he signs off.
Reviews folded in:
1. code-owl architecture review (Claude Opus 5.5): client plus caller-owned specs.
2. GPT-6 Astra plan review: separate answers from permission, blind shadow, complete runs, contract 1.3.
3. GPT-6 Astra code-owl plan review of the install model: a separate stage is the default socket, drift checks run in a script, build less first.
4. Claude Sonnet and GPT-6 Astra code-owl plan reviews of a harness-level interceptor: note only, replacement deferred.

## 1. Problem

Several skills apply the same fixed-answer judgment to hundreds of items: search terms sorted into five actions, keywords tagged by intent, 1,500 name candidates filtered, candidate URLs tagged by site type. Today the premium LLM (Opus) does this item by item. That costs tokens in proportion to the item count, and judgment drifts over long lists.

classifier-skill already sends such judgments to TypeSafe Jev, a non-generative model that answers typed questions (`choice`, `noul`, `score`) with probabilities. But:

- The skill rarely triggers on its own inside other skills' workflows (usage analysis, 2026-09-25: missed opportunities, not bad results). The nudge hook helps only when work goes through a sub-agent.
- The caller contract (`references/callers.md`, v1.2) leaves each caller to write its own lookup, version check, batch handling, parsing, and fallback in prose.
- Nobody has measured whether Jev is as accurate as Opus on these tasks. Wiring code-owl to it did not help, because code review is open reasoning, not a repeated fixed-answer judgment.
- Integration must scale to someone else's setup: a user with 30 skills, 15 eligible, should not write custom code 15 times.
- Many skills update from someone else's repository, a plugin marketplace, or the claude.ai sync folder (`~/.claude/skills/synced/`). Any line added to such a skill is erased on update.

## 2. Goals

1. One reusable piece of software plugs into any eligible skill. The per-skill part is data (a question sheet), not code.
2. Third-party skills are never edited.
3. Opus tokens per 100 judged items drop substantially on wired tasks.
4. Accuracy is measured per task before the classifier's answers change anything.
5. Every run is auditable: how many items the classifier answered, how many went to review, how many fell back to the LLM, and why.
6. Skills stay independent: the classifier knows nothing about its callers, and a caller works (more slowly) when the classifier is missing.

## 3. Non-goals

- Replacing Opus for open reasoning, writing, tool use, or single-item judgment.
- Code review, landing-page audits, or other per-item work that needs fetching or deep reading.
- A workflow language in the question sheet. Callers keep their own domain rules.
- Replacing tool results in the harness (deferred; section 10).
- An automatic onboarding tool before two callers exist (deferred; section 13).
- A local-model path now. `--local-only` stays as built; see `references/providers.md`.

## 4. When a task goes to the classifier

A task step qualifies only when all of these hold:

- The answer set is fixed (`choice`, `noul`, or `score`).
- The same question is asked of at least `min_items` items (default 20, tuned by measurement).
- Each item can be judged from a short card of vetted facts, with no fetch and no deep read.
- A wrong answer is cheap to undo, or low-confidence items go to a human.

Below `min_items`, the LLM judges inline. That is an expected outcome, not a failure.

## 5. Callers

| Order | Caller skill | Step | How the list arrives | Volume |
|---|---|---|---|---|
| 1 | gta-account-optimization | task T13: classify every query with spend (T14 same shape) | Google Ads MCP result | ~400 a week |
| 2 | google-ads-tuning | wasted-spend sweep, keyword intent, employment filter | Google Ads MCP result | hundreds per account |
| 3 | name-generator | step 4 evaluation, 1,500+ down to 50-80 | the model generates it | ~1,500 |
| 4 | event-calendar-discovery | phase 2 site tagging, event-fit filter | search and fetch results | 20-80 |
| deferred | chatgpt-fanout, topical-map-generator | source tagging; keyword to cluster | tool results | unknown, wire only with volume data |

Callers live in other repositories and publish on their own cadence. Each caller change needs the owner's go-ahead at the time. Caller coverage above comes from reading the callers' SKILL.md files, not from running them.

## 6. Data

All data and maps that existed on 2026-09-26, including client advertising data, may go to cloud LLM and AI services (owner's decision, 2026-09-26). So question sheets for existing data use `data: cloud_ok`.

Still required:
- Secrets are redacted before sending (built, contract 1.2).
- Client names stay out of logs and learnings.
- A new data source added later, or anything the owner marks local-only, needs a fresh decision; `data: local_only` then makes the client enforce `--local-only`.
- A runtime setting may tighten a sheet's data rule, never loosen it.
- Row text is third-party data. Cards carry only allowlisted fields, labeled as data; nothing in a row can set a command, path, endpoint, or policy.
- Fallback judgment is done by the session that already holds the data, so it sends the data nowhere new.

## 7. Architecture

### 7.1 The picture

Every eligible job has the same shape: a list exists, each item gets the same question, the answers go to the next step. So the classifier always sits **between two steps**: it takes a list out of one step and hands a stamped list into the next. "Before a skill", "inside a skill", and "after a skill" are the same socket in different places.

One reusable piece, the **card maker** (`classify_items.py`), does the work wherever it is plugged in. The only per-skill piece is a **question sheet**: a data file.

### 7.2 Ownership

| Concern | Owner |
|---|---|
| Script lookup, contract version check | client (published lookup order) |
| Cards from rows, batching, size check, redaction, provider, retries, pacing | client |
| Stable ids, input and output reconciliation, completion record, reason codes | client |
| Stored original and retrieval of any row by id | client |
| Per-question disposition from a per-question threshold | client (mechanics only) |
| Drift check (source fingerprint) | client, never the LLM |
| Questions and options, card fields, data rule, `min_items`, model pin, source binding | question sheet |
| How answers combine into an action, per-action thresholds | caller's existing rules |
| Status ladder (shadow, advisory, auto) and what answers may change | caller, recorded with the sheet |
| Judging fallback items | the LLM running the skill |

### 7.3 Where to plug in (sockets)

Choose by one question: **where does the list first exist as data?**

| Rank | Socket | Use when | Edits to the skill |
|---|---|---|---|
| 1 | **Separate stage (default)**: a companion workflow fetches or receives the list, stores the original, runs the card maker, and hands the skill a stamped list plus a retrieval handle as ordinary input | the list exists, or can be fetched, before the skill judges it | none |
| 2 | **One line in the skill** | the owner authors the skill | one line |
| 3 | **Wrapper skill**: a small skill the owner owns runs the upstream skill and plugs in the card maker at the step | a third-party skill whose list only appears mid-run | none to the upstream skill |
| 4 | **Overlay hook** (experimental) | only after host tests pass (section 10) | none |

Examples: gta T13 fetches its own search terms, but a stage can fetch them first (same MCP call, or a small export), which moves it to socket 1. name-generator creates its list, so it needs socket 2 (owned) or writes the list to a file for a stage to pick up.

After-the-skill use gives a second opinion but saves no tokens, because the LLM has already judged everything.

Never edit a skill the owner does not author. Fork and patch, an upstream pull request adding an optional "if classifier-skill is installed" line, and version pinning are fallbacks the owner may choose; the plan does not depend on them.

### 7.4 Alternatives considered

| Option | Verdict |
|---|---|
| Each caller embeds lookup, version check, batch, parse, and fallback prose | Rejected: duplicated, drifts, erased by third-party updates |
| Shared client that also owns routing and the status ladder | Narrowed: mixes answer confidence with permission to act |
| Classifier keeps a registry of callers | Rejected: reverses the dependency |
| Nudge hook as the main mechanism | Rejected: measured missed use. Kept, and extended to tool results as notes (section 10) |
| Harness module that replaces list results with stamped summaries | Deferred: both harness reviews (section 10) |
| **Card maker plus data-only sheets, plugged in at a ranked socket (chosen)** | Shared routing beyond this is decided after the second caller (Rule of Three) |

## 8. Component spec

### 8.1 Card maker: `scripts/classify_items.py`

```
python3 classify_items.py --sheet SHEET.json --items ITEMS.jsonl --out OUT.jsonl --summary SUMMARY.json \
    [--check-sheet] [--deadline SECONDS] [--dry-run]
python3 classify_items.py --get RUN_ID ID [ID ...]
```

- Validates the sheet and the items, then calls Jev through the same internals as `jev_decide.py` (no second copy of transport, validation, or redaction).
- Stores the original items under the run id before sending anything. `--get` returns original rows by id, bypassing the classifier.
- Writes one output line per input item, in input order. Stdout, when used, carries item lines only.
- Writes the summary file last. A missing summary, `complete: false`, or a count mismatch means the run is incomplete, and the caller uses the original list.
- `--deadline` bounds the whole run including retries; items not reached get `reason: not_reached`.
- Bounded concurrency under a credential-wide pace, so hundreds of items finish in reasonable time without exceeding the rate limit.
- `--check-sheet` validates the sheet, the contract major version, and the source fingerprint, and dry-runs the sheet's fixture. It sends nothing and needs no key.

### 8.2 Question sheet (data only)

Owner-maintained. Lives in the skill's folder for owned skills, or in `~/.config/classifier-skill/sheets/<skill>/` for third-party skills.

```json
{
  "sheet": "gta-search-terms",
  "version": 1,
  "contract": "1.3",
  "data": "cloud_ok",
  "min_items": 20,
  "model": "typesafe/jev-1.13",
  "source": {
    "skill": "gta-account-optimization",
    "step": "### T13",
    "files": ["references/tasks.md"],
    "fingerprint": "sha256:..."
  },
  "fields": {"id": "search_term", "card": ["search_term", "campaign", "ad_group", "match_type"]},
  "fixture": "search-terms.fixture.jsonl",
  "questions": {
    "action": {"type": "choice", "instructions": "...", "criteria": {"...": "..."}, "threshold": 0.8},
    "irrelevant": {"type": "noul", "instructions": "...", "criteria": {"true": "...", "false": "..."}, "threshold": 0.9}
  },
  "consumes": "T13 applies its five-action rules to `action`; `irrelevant` true with disposition answered supports a negative; `human` rows are listed for the owner."
}
```

- `fields` is the input mapping: which row field is the id and which fields go on the card. Only these fields are sent.
- `consumes` is the output mapping, in plain words: how the step's existing rules use each question's result. If the step cannot use them without edits, the sheet says so and the skill needs socket 2 or 3.
- `choice` questions include "none fit" and "insufficient context" options where the task allows; both route to review.
- Editing questions or fields means a new `version`. The sheet id and version go into the call log.
- `model` pins the model the calibration was done on.

### 8.3 Drift check

A third-party update can rename or rewrite the step a sheet points at.

- The card maker (a script, never the LLM) resolves the installed skill, finds the uniquely bounded step in the named files, and hashes that text plus the listed dependency files, before any cloud call.
- Only whitespace and line-ending differences are normalized away.
- A missing, ambiguous, unreadable, or changed source skips the classifier: every item gets `reason: source_changed`, and the LLM judges as before.
- A changed source, sheet version, model, or field mapping sends the sheet back to shadow.

### 8.4 Items and output

Items: one JSON object per line. Ids must be unique; duplicates exit 2.

Output line:

```json
{"id": "term-0042", "status": "answered",
 "answers": {"action": {"choice": "exact_negative", "probabilities": {}, "confidence": 0.91}, "irrelevant": {"noul": 0.94}},
 "dispositions": {"action": "answered", "irrelevant": "answered"},
 "review": {}}
```

- `status`: `answered` or `unanswered` (with `reason`).
- `dispositions`, per question: `answered`, `skip` (a `noul` confidently false), or `human`.
- A disposition is never permission to act; the caller's rules decide.

### 8.5 Reason codes

| Reason | Kind |
|---|---|
| `below_min_items` | expected bypass |
| `source_changed` | expected bypass (drift) |
| `not_reached` (deadline) | degraded |
| `no_key`, `transport`, `refused_host` | degraded |
| `bad_request`, `too_large` | degraded (caller or sheet bug) |
| `invalid_answer` | degraded |

### 8.6 Summary file

```json
{"sheet": "gta-search-terms", "version": 1, "run_id": "...", "complete": true,
 "items_in": 412, "items_out": 412, "answered": 380, "human_questions": 41,
 "unanswered": {"transport": 0, "invalid_answer": 2},
 "bypass": false, "degraded": true, "model": "typesafe/jev-1.13",
 "cost": 0.0, "seconds": 0.0}
```

Counts per question and the item-level summary are kept separate. `human` (needs the owner) is never merged with LLM fallback.

### 8.7 Plugging in

The procedure (find the script, run it, check the summary, fall back, print the count line) is written once, in classifier-skill's SKILL.md, as a `judge` section. A socket only says where the list is and which sheet applies.

- **Separate stage:** the companion workflow runs `judge` on the list and passes the output and summary to the skill as input.
- **One line (owned skills):** "Use classifier-skill `judge` with `classifier/search-terms.v1.json` on the items; apply this step's rules to the results."
- **Wrapper:** the wrapper's SKILL.md invokes the upstream skill and, at the named step, runs `judge`. The wrapper's sheet carries the upstream fingerprint; a test must show `judge` runs before the upstream step's own judgment.

Every run ends with `Classifier: <answered>/<human>/<unanswered> (<reasons>)`. usage-analyst audits this line; a missing line means the step was skipped.

### 8.8 Chains

Items in, stamped items out, original retrievable by id. Totals, cost sums, word-group counts, and deduplication are computed by code over the full data, never over the stamped subset. The LLM reads the summary plus `human` and `unanswered` rows; that is where the token saving comes from.

## 9. Accuracy: shadow and calibration

Before a caller lets answers change anything, it runs in **shadow**:

1. The LLM's judgment is captured **blind**: in an isolated context with classifier hooks off, from the same vetted facts, and committed before any classifier output is shown. The two are joined by id afterward.
2. A labeled sample gets independent human labels (for gta, past decisions from change history), with any field that reveals the outcome removed.
3. Both arms are scored against the labels across all items and all classes.
4. Thresholds are tuned on one split and reported on a held-out split (the `calibrate` recipe).
5. Report per class: accuracy, coverage at the threshold, Opus tokens per 100 items, cost, time.
6. Evidence is bound to the sheet version, model, field mapping, and source fingerprint.

Promotion is the caller's decision: shadow, then advisory (answers drive proposals; a human approves consequential actions), then auto only for reversible actions whose per-class accuracy clears the caller's target. Costlier mistakes (a phrase negative) need a higher bar than cheap ones (ignore). Installing a sheet and promoting it are separate approvals.

## 10. Harness hooks

### 10.1 Verified facts (Claude Code docs, 2026-09-26)

- A PostToolUse hook can replace a tool's result with `updatedToolOutput` (all tools) or `updatedMCPToolOutput` (MCP only). MCP output is passed through without schema validation.
- Hook input has no field naming the active skill. A hook can match the `Skill` tool when the model calls it, but a user typing `/skillname` bypasses PreToolUse (it fires `UserPromptExpansion`). Nothing carries skill identity into a later, unrelated tool call.
- Matching hooks run in parallel, so no hook can rely on running after another (for example, after a token reducer).
- `additionalContext` from a hook reaches the model in the same turn.
- No equivalent hook system is documented for Codex.

### 10.2 What is built: note-only hooks

- The existing sub-agent nudge stays.
- New PostToolUse note hook for an allowlist of tools (for example, the Google Ads search-terms reports). When a result is list-shaped and a sheet names that tool as a source, it adds a note: "A classifier question sheet may apply: run `judge` with <sheet>". It never replaces the result and never calls the classifier itself.
- Watch logging, metadata only (tool, row count, matching sheet, whether `judge` later ran), to measure coverage.

### 10.3 Deferred: replacing results

Replacing a list with a stamped summary would save the most tokens, but both reviews found it unsafe now. Hiding confident rows turns stamps into decisions nobody can check; later steps need hidden rows; partial processing can look complete; hundreds of calls do not fit a hook's time budget; row text gains apparent authority; two hooks rewriting one result can erase each other; and blind shadow comparison breaks. It may be reconsidered only when all of these exist:

- a tested way to know which step's intent a tool call serves (not just which skill loaded)
- one replacement owner per tool, tested alongside the token reducers
- per-class held-out accuracy for that sheet, and regular audits of hidden rows
- a trace of every downstream consumer of the rows
- evidence from note mode that hidden rows never mattered downstream
- the original stored and verified retrievable before any replacement is published

## 11. Contract

The change adds to the contract: **1.3**. `jev_decide.py` and every 1.2 guarantee stay. 1.3 adds:

- `classify_items.py`, its lookup order (same folders as `jev_decide.py`, plus `$CLASSIFIER_ITEMS_SCRIPT`), and `--contract-version`.
- The sheet schema, items format, output line, reason codes, summary file, and `--get`.
- Rule: stdout carries item lines only.

The sheet schema version and each sheet's own `version` are separate from the contract version.

## 12. Testing

In classifier-skill (`tests/test_scripts.py`, new class `ItemsClient`), deterministic with a stub transport:

- every input id appears once in output, in order; duplicate ids exit 2
- transport failure mid-run: earlier items stay answered, later items get `transport` or `not_reached`, the summary has correct counts
- process killed mid-run: no summary file, so the caller rejects the run
- below `min_items`: every item `below_min_items`, no request sent
- drift: changed step text, missing step, ambiguous step, changed dependency file each give `source_changed` and send nothing; whitespace-only change does not
- `data: local_only` with a cloud endpoint: refused before sending
- only the sheet's card fields are sent
- invalid answer on one item: only that item is `invalid_answer`
- per-question dispositions match thresholds; a `review` flag forces `human`
- `--get` returns the stored original rows
- `--check-sheet` on good, bad, and wrong-major sheets
- note hook: fires only for allowlisted tools with a matching sheet, never sets `updatedToolOutput`, fails open

In each caller or wrapper: one fixture and one `--check-sheet` run.

## 13. Build plan

| Phase | Work | Done when |
|---|---|---|
| 0 | Fix `jev_decide.py` batch partial failure: a transport error mid-batch exits 1 after printing earlier lines, with no stderr summary and no call-log entry (`run_batch`, the `call_jev` call inside the loop) | regression test passes |
| 1 | Card maker (`classify_items.py`), sheet schema, drift check, stored originals and `--get`, `--check-sheet`, `judge` section in SKILL.md, contract 1.3, tests in section 12 | all tests pass; code-owl review clean; installed copy synced |
| 2 | gta T13 sheet, written by hand, plugged in through a separate stage (or a wrapper if a stage cannot fetch the terms), in shadow. Needs owner go-ahead | two to three weeks of real runs with a blind LLM arm |
| 3 | Calibrate T13 against human labels; caller decides on advisory | per-class report; decision recorded |
| 4 | google-ads-tuning sheet. Rule of Three: move any routing both callers repeat into the card maker, or confirm it stays per caller | second caller in shadow; interface decision written down |
| 5 | Note-only PostToolUse hook and watch logging | coverage and "judge ran after note" measured |
| 6 | name-generator (owned, one line), event-calendar-discovery (synced copy: stage or wrapper) | each in shadow, then calibrated |
| later | `onboard` tool (drafts sheets from step text; approval shows source excerpts, question and option mapping, card fields, data rule, thresholds, fallback, fixture results, exact install change); overlay hook; result replacement (section 10.3); resume from partial output | only after two callers and measured need |

## 14. Metrics

- Opus tokens per 100 judged items, before and after, per caller.
- Per-class accuracy of the classifier and of the blind LLM against human labels.
- Items answered, sent to review, and unanswered, with reason counts. Any degraded run is flagged; `unanswered` over 10% for a degraded reason is investigated.
- Drift skips per sheet.
- Note-hook coverage: notes shown, and how often `judge` ran afterward.
- Callers wired, by socket and status.

## 15. Risks

| Risk | Mitigation |
|---|---|
| Calibration is tied to one model | sheet pins `model`; a change sends the caller back to shadow |
| Third-party update erases or changes the step | never edit third-party skills; script-run fingerprint skips the classifier on change |
| Silent fallback | summary file, reason counts, count line; usage-analyst checks degraded runs |
| Shadow agreement mistaken for accuracy | blind arm committed first, independent labels, all items scored, held-out split |
| Answer confidence treated as permission to act | dispositions never authorize; caller rules and status decide |
| Later steps need rows the LLM did not read | original stored with retrieval by id; totals computed by code over full data |
| Row text steers the classifier or the model | allowlisted card fields, labeled as data; answers validated; summaries rendered by code |
| Card maker grows into a workflow engine | Rule of Three at phase 4; sheets hold questions, fields, and data rules only |
| Wrapper runs after the upstream step already judged | test that `judge` runs first; wrapper bound to the upstream fingerprint |
| Caller installed where classifier-skill is not | published lookup order; missing script means inline judgment, stated in the count line |
| Harness hook collides with token reducers | note-only hook never rewrites results |
| Rate limit (1,200 requests a minute) | credential-wide pacing across concurrent runs |

## 16. Open questions

1. Accuracy target per caller and per class (owner to set before phase 3).
2. Source of human labels for gta T13 and how outcome-revealing fields are stripped.
3. Whether a separate stage can fetch gta's search terms directly, or T13 needs a wrapper.
4. Whether the Google Ads skills also run under Codex or on other machines.
5. Jev cost per 1,000 items at these card sizes (measure in phase 2).
6. Concurrency level that keeps 1,500 items fast without hitting the rate limit.
