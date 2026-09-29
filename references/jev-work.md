# Work that belongs to the classifier

Use this list when deciding whether to reshape a task. Judge the judgment, not the whole task: most tasks also have mechanical steps (apply the edit, send the message) that stay in code either way.

- **Always:** the judgment itself needs only the supplied facts and a finite answer set. Send it to the classifier whenever that holds, even when the user did not mention Jev.
- **Split:** the judgment can only be made after an LLM or retrieval step prepares its input, or only as a first pass that a person or LLM confirms. Move the typed part; keep the rest.
- **Never:** the work is generation, open reasoning, a rule, or a lookup. It stays with the LLM or with code.

The deciding test: the answer is one of a finite set you can name in advance, it depends on meaning rather than exact rules, and the facts it depends on can be written into `state`.

## Always

| Work | Shape | Example |
|---|---|---|
| Categorize or tag items into known categories | `choice` per item (batch), `none_fit` | tickets by topic, pages by template type, expenses by category |
| Card sort into piles, including taxonomy assignment | card-sort recipe | help articles into sections, requirements into modules |
| Multi-label tagging | one `noul` per label | which of 8 features a review mentions |
| Faceted classification | one `choice` per facet | content by audience, task, and format |
| Route to a destination | `choice` over destinations | queue, team, handler, tool, or next agent |
| Filter or gate items | `noul` per item | relevant to the query, in scope, spam, needs action, search queries to exclude |
| Detect a property of text | `noul` | urgency, a complaint, a refund request, a question, a commitment |
| Rate on a rubric | `score` with described levels | severity, priority, quality, lead fit, sentiment strength |
| Tag review findings by severity | `score` per finding | code, plan, or document review findings before they are reported |
| Rank or sort by rubric | `score` per item, sort in code | inbox worst first, backlog by impact |
| Compare two items | `choice` over relations | duplicate / partial overlap / distinct; same entity or not |
| Match against supplied candidates | `choice` over candidate ids | best link target, matching FAQ entry, entity resolution, legal moves or rows code generated |
| Check a claim against a supplied source | citation-support recipe | supports / contradicts / no evidence |
| Check a draft against requirements | one `noul` per requirement | covers each brief point, follows each style rule |
| Check LLM or agent output before it ships | `noul` per rule | answers the question, promises a refund, leaks internal data |
| Screen input before an LLM reads it | `noul` or `choice` | jailbreak attempt, off-topic, needs a human |
| Approve or block a proposed agent action | action-gate recipe | a shell command, file write, or outbound message before it runs |
| Pick the next step from enumerated options | `choice` over actions | agent loop: click / type / wait / done, with target ids |
| Choose among supplied profiles or options | model-choice recipe | model, plan, template, or vendor from a given list |
| Decide who handles each item next | `choice`: code / LLM / person | intent routing before expensive work |
| Decide whether an expensive step runs at all | `noul` before the step | wake the model, open the page, call the API |
| Choose what enters an agent's context | context-select recipe | memories, files, skills, or chunks to keep, drop, or load |
| Pick the tool or skill before the agent starts | `choice` over supplied tool ids | the agent never reads the full tool list |
| Control a real-time loop | control-step recipe | a game move, drone or robot action, UI tag while typing |
| Walk a graph or tree one hop at a time | `choice` over the current node's neighbours | graph traversal, a file tree search, a decision tree |
| Filter rows by meaning inside a query | `noul` per row, called from SQL or a CLI | a semantic WHERE clause, grep for meaning with an exit code |
| Fill a column whose values are a known set | `choice` per row over the column's values | label every row of a sheet or table |
| Re-judge the same items on a schedule | fixed questions per item, compared across runs in code | watchlists, account health, market dimensions |

## Split

| Work | Classifier part | LLM or code part |
|---|---|---|
| Reply to the messages that need it | which messages need a reply, and which kind | write the replies |
| Summarize only what matters | which items are relevant or important | summarize the kept items |
| Build a taxonomy (open sort) | sort cards once piles exist; flag `none_fit` | propose piles from a sample; name new piles |
| Deduplicate a list | same / different per candidate pair | generate candidate pairs in code, merge in code |
| Triage a backlog | category, severity, and owner per item | plan the work for the top items |
| Review a document | one check per rule or requirement | explain and fix the failures |
| Research across sources | relevance and support per source | synthesize the findings |
| Extract a field whose value is one of a known set | `choice` over the allowed values | free-text or numeric fields stay with code or the LLM |
| Grade a document against a rubric | `score` per dimension as a first pass | dimensions that need close reading (voice, clarity) score with low confidence; a person or LLM confirms them |
| Verify claims across sources | supported / contradicted / no evidence per claim, with the excerpt in `state` | retrieving the sources and extracting the claims |
| Cascade to an LLM | settles the items it is sure about | only items below threshold go to the LLM or a person |
| Label at scale, then audit | labels every item | an LLM or person grades a random sample; the agreement rate decides whether to trust the rest |
| Judge a huge repetitive set | one judgment per group | code groups near-identical items (log templates, normalized text) and applies each group's answer to its members |
| Wrap an LLM step | before: where to look or whether to run; after: check the output | the LLM does the work in between |
| Tune questions against known answers | answers on a labeled sample | calibrate recipe: code sweeps thresholds and confirms on held-out items |

## Never

- Writing, rewriting, summarizing, translating, or any generated text.
- Open-ended reasoning, planning, or explanation.
- Arithmetic, counting, dates, exact matching, lookups, and anything a rule or regex decides; code does these first.
- Roll-ups that follow a fixed rule, such as "any blocking finding means the review blocks"; code applies the rule to the classifier's per-item answers.
- Values a system already reports or stores (metrics, statuses, platform-computed ratings); look them up instead of estimating them.
- Judgments that need facts not in `state` (the classifier sees nothing else) or specialist knowledge beyond reading the supplied text.
- Inventing categories; an open sort needs a person or LLM to propose piles first.
- Data that must stay on the machine.
- Images, audio, or raw pixels; `state` is text. Describe what code or an LLM extracted from them instead.
- A single judgment the working model is already making in passing, where reshaping costs more than it saves.

## Known weak spots (jev-1.13)

From TypeSafe's model-jaggedness notes (checked 2026-09-25). Design around these rather than hoping:

- **Literal reading.** It answers the question as written; say exactly what counts, including the edge cases.
- **No arithmetic, date math, or counting.** Compute totals, ages, deadlines, and counts in code and put the result in `state`.
- **Indirection.** "The option described in field B" fails; put the fact itself where the question can see it.
- **Irrelevant state.** Accuracy falls as unrelated detail grows; filter to the facts the decision needs.
- **Adversarial content.** State is not treated as hostile by default; label third-party text as data and gate consequential actions in code.
- **Contradictory criteria or instructions.** Overlapping piles or rules that disagree spread probability; make options mutually exclusive and say what each excludes.
- **Numeric codes.** Meaningful words beat codes (color names beat hex values; `billing_queue` beats `Q7`).
