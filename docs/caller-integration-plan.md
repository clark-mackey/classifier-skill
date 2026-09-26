# PRD and build plan: calling the classifier from other skills

Status: approved design, not built. Written 2026-09-26.
Reviews: code-owl architecture review (Claude Opus 5.5), then an independent plan review (GPT-6 Astra). Both are folded in below.

## 1. Problem

Several skills apply the same fixed-answer judgment to hundreds of items: search terms sorted into five actions, keywords tagged by intent, 1,500 name candidates filtered, candidate URLs tagged by site type. Today the premium LLM (Opus) does this item by item. That costs tokens in proportion to the item count, and judgment drifts over long lists.

classifier-skill already sends such judgments to TypeSafe Jev, a non-generative model that answers typed questions (`choice`, `noul`, `score`) with probabilities. But:

- The skill rarely triggers on its own inside other skills' workflows (usage analysis, 2026-09-25: missed opportunities, not bad results). The nudge hook helps only when work goes through a sub-agent.
- The caller contract (`references/callers.md`, v1.2) leaves each caller to write its own lookup, version check, batch handling, parsing, and fallback in prose. Six callers would mean six drifting copies, and weaker models skip prose fallbacks.
- Nobody has measured whether Jev is as accurate as Opus on these tasks. Wiring code-owl to it did not help, because code review is open reasoning, not a repeated fixed-answer judgment.

## 2. Goals

1. Domain skills can hand repeated fixed-answer judgment to the classifier through one short, reliable step.
2. Opus tokens per 100 judged items drop substantially on wired tasks.
3. Accuracy is measured per task before the classifier's answers change anything.
4. Every run is auditable: how many items the classifier answered, how many went to review, how many fell back to the LLM, and why.
5. Skills stay independent: the classifier knows nothing about its callers, and a caller works (more slowly) when the classifier is missing.

## 3. Non-goals

- Replacing Opus for open reasoning, writing, tool use, or single-item judgment.
- Code review, landing-page audits, or other per-item work that needs fetching or deep reading.
- A workflow language in the spec file. Callers keep their own domain rules.
- A local-model path now. `--local-only` stays as built; see `references/providers.md`.

## 4. When a task goes to the classifier

A task step qualifies only when all of these hold:

- The answer set is fixed (`choice`, `noul`, or `score`).
- The same question is asked of at least `min_items` items (default 20, tuned by measurement).
- Each item can be judged from a short card of vetted facts, with no fetch and no deep read.
- A wrong answer is cheap to undo, or low-confidence items go to a human.

Below `min_items`, the LLM judges inline. That is an expected outcome, not a failure.

## 5. Callers

| Order | Caller skill | Step | Items | Volume |
|---|---|---|---|---|
| 1 | gta-account-optimization | task T13: classify every query with spend (T14 same shape) | search terms | ~400 a week |
| 2 | google-ads-tuning | wasted-spend sweep, keyword intent, employment filter | keywords and search terms | hundreds per account |
| 3 | name-generator | step 4 evaluation, 1,500+ down to 50-80 | name candidates | ~1,500 |
| 4 | event-calendar-discovery | phase 2 site tagging, event-fit filter | candidate URLs | 20-80 |
| deferred | chatgpt-fanout, topical-map-generator | source tagging; keyword to cluster | varies | unknown, wire only with volume data |

Callers live in other repositories and publish on their own cadence. Each caller edit needs the owner's go-ahead at the time.

## 6. Data

All data and maps that existed on 2026-09-26, including client advertising data, may go to cloud LLM and AI services (owner's decision, 2026-09-26). So caller specs for existing data use `data: cloud_ok`.

Still required:
- Secrets are redacted before sending (built, contract 1.2).
- Client names stay out of logs and learnings.
- A new data source added later, or anything the owner marks local-only, needs a fresh decision; `data: local_only` then makes the client enforce `--local-only`.
- A runtime setting may tighten a spec's data rule, never loosen it.
- Fallback judgment is done by the session that already holds the data, so it sends the data nowhere new.

## 7. Architecture

### Decision

One reliability-focused client script in classifier-skill. Each caller owns a small versioned spec file with its questions, plus its own action rules. The dependency points one way: callers depend on the published contract; the classifier never names a caller.

### Alternatives considered

| Option | Verdict |
|---|---|
| A. Each caller embeds lookup, version check, batch, parse, and fallback prose | Rejected: duplicated in every caller, drifts, prose fallbacks get skipped |
| B. Shared client that also owns routing, thresholds, and the shadow/advisory/auto ladder | Narrowed: the Astra review showed it mixes answer confidence with permission to act, and one row route cannot express per-question answers or unequal error costs |
| C. Classifier keeps a registry of callers and their questions | Rejected: reverses the dependency and ties caller releases to classifier releases |
| D. Rely on the nudge hook | Rejected as the main mechanism: measured missed use. Kept for unwired skills |
| **E. Client owns reliability; callers own routing and action rules (chosen)** | Merges B with Astra's alternative. Whether more routing is shared is decided after the second caller (Rule of Three) |

### Ownership

| Concern | Owner |
|---|---|
| Script lookup, contract version check | client (published lookup order) |
| Batching, size check, redaction, provider, retries, pacing | client |
| Stable ids, output and input reconciliation, completion record, reason codes | client |
| Per-question disposition from a per-question threshold | client (mechanics only) |
| Questions and options, data rule, `min_items`, model pin | caller spec file |
| How answers combine into an action, per-action thresholds | caller |
| Status ladder (shadow, advisory, auto) and what answers may change | caller |
| Judging fallback items | caller's LLM |

## 8. Component spec

### 8.1 Client: `scripts/classify_items.py`

```
python3 classify_items.py --spec SPEC.json --items ITEMS.jsonl --summary SUMMARY.json \
    [--check-spec] [--deadline SECONDS] [--dry-run]
```

- Reads the spec and the items, validates both, then calls Jev through the same internals as `jev_decide.py` (no second copy of transport, validation, or redaction).
- Writes one JSON line per input item to stdout, in input order, and nothing else to stdout.
- Writes the summary file last. A missing summary, `complete: false`, or a count mismatch means the run is incomplete.
- `--deadline` bounds the whole run; items not reached get `reason: not_reached`.
- `--check-spec` validates the spec and the contract major version, and dry-runs a fixture if the spec names one. It sends nothing and needs no key.

### 8.2 Spec file (caller-owned)

Path inside the caller, for example `gta-account-optimization/classifier/search-terms.v1.json`:

```json
{
  "spec": "gta-search-terms",
  "version": 1,
  "contract": "1.3",
  "data": "cloud_ok",
  "min_items": 20,
  "model": "typesafe/jev-1.13",
  "fixture": "search-terms.fixture.jsonl",
  "questions": {
    "action": {"type": "choice", "instructions": "...", "criteria": {"...": "..."}, "threshold": 0.8},
    "irrelevant": {"type": "noul", "instructions": "...", "criteria": {"true": "...", "false": "..."}, "threshold": 0.9}
  }
}
```

- `choice` questions should include a "none fit" and an "insufficient context" option where the task allows.
- Editing questions means a new `version`. The spec id and version go into the call log, so calibration results never carry over to a changed spec.
- `model` pins the model the calibration was done on. A different model makes the caller treat the spec as unvalidated (back to shadow).

### 8.3 Items file

One JSON object per line: `{"id": "<stable id>", "state": "<vetted facts for this item>"}`. Ids must be unique; the client refuses duplicates with exit 2.

### 8.4 Output line

```json
{"id": "term-0042", "status": "answered",
 "answers": {"action": {"choice": "exact_negative", "probabilities": {}, "confidence": 0.91}, "irrelevant": {"noul": 0.94}},
 "dispositions": {"action": "answered", "irrelevant": "answered"},
 "review": {}}
```

- `status`: `answered` (the classifier returned valid answers) or `unanswered` (with `reason`).
- `dispositions`, per question: `answered`, `skip` (a `noul` confidently false), or `human` (below threshold, or flagged in `review`).
- The caller turns dispositions into actions under its own rules. A disposition is never permission to act.

### 8.5 Reason codes

| Reason | Kind |
|---|---|
| `below_min_items` | expected bypass |
| `not_reached` (deadline) | degraded |
| `no_key`, `transport`, `refused_host` | degraded |
| `bad_request`, `too_large` | degraded (caller bug) |
| `invalid_answer` | degraded |

### 8.6 Summary file

```json
{"spec": "gta-search-terms", "version": 1, "run_id": "...", "complete": true,
 "items_in": 412, "items_out": 412, "answered": 380, "human_questions": 41,
 "unanswered": {"transport": 0, "invalid_answer": 2},
 "bypass": false, "degraded": true, "model": "typesafe/jev-1.13",
 "cost": 0.0, "seconds": 0.0}
```

### 8.7 Caller step

The whole step as it appears in a caller's SKILL.md:

> Find `classify_items.py` by the lookup order in classifier-skill's `references/callers.md`. Run it with `--spec <this skill's folder>/classifier/search-terms.v1.json --items <file> --summary <file>`. If the script is missing or the summary is missing or incomplete, judge every item yourself and say so. Otherwise apply this skill's action rules to answered items, list `human` items for review, and judge `unanswered` items yourself. End with `Classifier: <answered>/<human>/<unanswered> (<reasons>)`.

Paths resolve from the caller skill's own folder, so the step works under Claude Code and Codex. The count line lets usage-analyst audit runs; a missing line means the step was skipped.

### 8.8 Chains

Items JSONL in, output JSONL out. The next stage reads the file. The LLM reads only the summary and the `human` and `unanswered` rows; that is where the token saving comes from.

## 9. Accuracy: shadow and calibration

Before a caller lets answers change anything, it runs in **shadow**:

1. The client answers every item; the caller's LLM also judges every item, **blind**: in a separate sub-agent or context that never sees the classifier's output, from the same vetted facts.
2. A labeled sample gets independent human labels (for gta, past decisions from change history), with any field that reveals the outcome removed from `state`.
3. Both arms are scored against the labels across all items and all classes, not only disagreements or low-confidence rows.
4. Thresholds are tuned on one split and reported on a held-out split (the `calibrate` recipe).
5. Report per class: accuracy, coverage at the threshold, Opus tokens per 100 items, cost, time.

Promotion is the caller's decision, recorded in the caller: shadow, then advisory (answers drive proposals; a human approves consequential actions), then auto only for reversible actions whose per-class accuracy clears the caller's target. Costlier mistakes (a phrase negative) need a higher bar than cheap ones (ignore).

## 10. Contract

The change adds to the contract: **1.3**. `jev_decide.py` and every 1.2 guarantee stay. 1.3 adds:

- `classify_items.py`, its lookup order (same folders as `jev_decide.py`, plus `$CLASSIFIER_ITEMS_SCRIPT`), and `--contract-version`.
- The spec schema, items format, output line, reason codes, and summary file above.
- Rule: stdout carries item lines only.

The spec schema version and each spec's own `version` are separate from the contract version.

## 11. Testing

In classifier-skill (`tests/test_scripts.py`, new class `ItemsClient`), deterministic with a stub transport:

- every input id appears once in output, in order; duplicate ids exit 2
- transport failure mid-run: earlier items stay answered, later items get `transport` or `not_reached`, the summary says `complete: true` with correct counts (the run finished; the degradation is recorded)
- process killed mid-run: no summary file, so the caller rejects the run
- below `min_items`: every item `unanswered: below_min_items`, `bypass: true`, no request sent
- `data: local_only` with a cloud endpoint: refused before sending
- invalid answer on one item: only that item is `unanswered: invalid_answer`
- stdout carries item lines only
- per-question dispositions match thresholds; a `review` flag forces `human`
- `--check-spec` on good, bad, and wrong-major specs

In each caller: one fixture and one `--check-spec` run in the caller's own tests.

## 12. Build plan

| Phase | Work | Done when |
|---|---|---|
| 0 | Fix `jev_decide.py` batch partial failure: a transport error mid-batch currently exits 1 after printing earlier lines, with no stderr summary and no call-log entry (`run_batch`, the `call_jev` call inside the loop). Record the partial run and say where it stopped | regression test passes; contract text unchanged or clarified |
| 1 | `classify_items.py`, spec schema, `--check-spec`, contract 1.3 in `callers.md`, tests in section 11 | all tests pass; code-owl review clean; installed copy synced |
| 2 | gta-account-optimization T13 spec and caller step, in shadow (needs owner go-ahead for that repo) | two to three weeks of real runs logged with blind LLM arm |
| 3 | Calibrate T13 against human labels; caller decides on advisory | report per class; decision recorded in the caller |
| 4 | google-ads-tuning, reusing the T13 question design. Rule of Three check: move any routing logic both callers repeat into the client, or confirm it stays per caller | second caller in shadow; interface decision written down |
| 5 | name-generator, event-calendar-discovery | each in shadow, then calibrated |
| later | Resume from partial output, bounded concurrency (only if runs are too slow; 1,500 sequential calls may take ~25 minutes), a shared answer-combination helper, retiring the nudge for wired callers | only with measured need |

## 13. Metrics

- Opus tokens per 100 judged items, before and after, per caller.
- Per-class accuracy of the classifier and of the blind LLM against human labels.
- Share of items answered, sent to review, and unanswered, with reason counts. Any degraded run is flagged; a run with `unanswered` over 10% for a degraded reason is investigated.
- Callers wired and running in each status.

## 14. Risks

| Risk | Mitigation |
|---|---|
| Calibration is tied to one model | spec pins `model`; a change sends the caller back to shadow |
| Silent fallback: every item falls back and nobody notices | summary file, reason counts, count line; usage-analyst checks degraded runs |
| Shadow agreement mistaken for accuracy | blind LLM arm, independent labels, all items scored, held-out split |
| Answer confidence treated as permission to act | dispositions never authorize; caller rules and status decide |
| Client grows into a workflow engine | Rule of Three at phase 4; spec holds questions and data rules only |
| Caller installed where classifier-skill is not | published lookup order; missing script means inline judgment, stated in the count line; never copy the classifier into a caller |
| Specs and `recipes.md` diverge | recipes are templates for specs; specs live with the caller and govern it |
| Rate limit (1,200 requests a minute) | sequential calls stay far under it; pacing required before any concurrency |

## 15. Open questions

1. Accuracy target per caller and per class (owner to set before phase 3).
2. Source of human labels for gta T13 and how outcome-revealing fields are stripped.
3. Whether the Google Ads skills also run under Codex or on other machines.
4. Jev cost per 1,000 items at these card sizes (measure in phase 2).
5. Whether 1,500 sequential calls for name-generator are fast enough, or phase "later" concurrency is needed sooner.
