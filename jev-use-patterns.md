# Ways to use a classifier: patterns from madewithjev.com

Created by [Clark Mackey](https://cakewebsites.com).

Source: all 759 projects on madewithjev.com (685 builds + 74 community submissions, crawled 2026-09-24). Both scouts, the browser one and the sitemap/source one, found the same URL set.
Method: Jev (`typesafe/jev-1.13`) card-sorted every project on 9 facets, run twice with reworded cards. Cost $0.15 total, 5 invalid lines dropped. A project counts as **placed** when the pile's confidence is ≥ 0.6.
Caveat: these are classifier judgments over site summaries. Project performance figures are author claims that the site does not verify. Links are relative to `https://madewithjev.com`.

## Findings

- **Use pattern** was stable. Of the projects placed in both runs, 476 stayed in the same pile and 2 moved.
- **Placed:** 438 of 757. Another 84 were `insufficient_context`, mostly thin cards such as X posts, launch news and repo roundups. 235 fell below threshold.
- **Biggest real piles:** real-time game/robot control (60), agent next-step (46), categorize (44), rate/rank (41), filter (41) and model routing (31). The infra/clone (47) and benchmark (21) piles are not uses.
- **Strong pairings:**
  - model routing ↔ runs before the LLM (18)
  - game control ↔ loop controller (12) and ↔ real-time (56)
  - categorize ↔ bulk pipeline (10)
  - verify ↔ runs after the LLM (7)
- **Rare but high-value patterns:** multi-question per item (30), confidence escalation (21) and replacing an LLM call outright (46).

## The list

"In skill?" says whether `references/jev-work.md` already covers the pattern. **NEW** marks the candidates to add.

### A. Sort and label (bulk)
| # | Pattern | In skill? | Exemplars |
|---|---|---|---|
| 1 | Categorize a corpus into fixed topics, then publish or act | yes | `/builds/1kpapers`, `/builds/invoice-accounting`, `/builds/docjev` (classify + split document packets) |
| 2 | Many small questions per item over a whole archive, rolled up in code | partly (facets) | `/builds/x-post-analysis` (8 questions × 3,282 posts), `/builds/viral-post-analyser` (14 yes/no per post), `/builds/every-editorial-judgments` (21 questions per doc) |
| 3 | Classifier labels, LLM audits a sample | **NEW** | `/builds/1000-papers-graded-by-opus` (Opus agreed on 85/100 at 153× the cost) |
| 4 | Multi-label tagging, including live as the user types | yes / **NEW** (UI-time) | `/builds/jev-pr-labeler`, `/builds/jev-auto-tagging` |
| 5 | Fill a column: name a field and the classifier sets it for every row | **NEW** | `/builds/predictive-spreadsheets` |

### B. Filter and read selectively
| # | Pattern | In skill? | Exemplars |
|---|---|---|---|
| 6 | Classify first, read selectively: gate what an agent opens | partly | `/builds/jev-sift`, `/builds/grok-thinks-jev-decides` |
| 7 | Group in code, then judge one representative per group | **NEW** | `/builds/tocsin` (22.8M log lines grouped into patterns, one question per pattern) |
| 8 | Screen leads or records against an ICP or spec | yes | `/builds/lead-screening-605m-tokens` |
| 9 | Personal feed or inbox filter (Read / Skim / Pass) | yes | `/builds/doomscroll-filter`, `/builds/firehose-judge` (with a lane for humans), `/builds/wip-todo-filter` |
| 10 | Classifier as a query operator: semantic WHERE / grep with an exit code | **NEW** | `/builds/postgres-jev-function`, `/builds/semdecide`, `/builds/margin-bookmark-search` |

### C. Route
| # | Pattern | In skill? | Exemplars |
|---|---|---|---|
| 11 | Pick the model, effort or provider before the LLM runs | yes (model-choice) | `/builds/jev-router-skill`, `/builds/on-device-audio-pipeline` |
| 12 | Pick the tool, skill or MCP call so the agent never sees the full list | partly | `/builds/jev-tool-router`, `/builds/slack-agent-skill-routing`, `/builds/home-assistant-typesafe` |
| 13 | Wake gate: should the expensive model run at all? | **NEW** | `/builds/jev-cursor-model-wake` |
| 14 | Cascade: the classifier takes the sure cases, an LLM takes the unsure ones | partly (thresholds) | `/builds/fraud-detection-jev-kimi` |

### D. Context and memory (whole pile **NEW**, 18 projects)
| # | Pattern | Exemplars |
|---|---|---|
| 15 | Pick which memories or files to load for this task | `/builds/jev-picks-the-memories` |
| 16 | Inject only the relevant skill or instructions | `/builds/skill-suggestion-for-context-bloat` |
| 17 | Compaction by deletion: keep or drop each chunk instead of summarising | `/builds/fast-jev-compaction` |

### E. Agent control loops
| # | Pattern | In skill? | Exemplars |
|---|---|---|---|
| 18 | Next step in a browser or computer-use loop | yes | `/builds/jev-ultrafast`, `/builds/computer-use-without-screenshots` (the screen is turned into text first) |
| 19 | Guided traversal: walk a graph or file tree, with each hop a choice | **NEW** | `/builds/neo4jev`, `/builds/blink` (100 walkers through a file tree) |
| 20 | Real-time control: a game, drone or robot calls the classifier many times a second | **NEW** (biggest pile, 60) | `/builds/jev-drone` (2.5 Hz), `/builds/jev-plays-puyo-puyo` (phase question, then a conditional landing question) |
| 21 | Code generates the legal candidates, the classifier filters or picks | **NEW** | `/builds/chess-with-a-defence-filter` (lost alone, won with the filter) |
| 22 | Re-score several dimensions on a clock and act on the change | **NEW** | `/builds/clawby-four-dimension-reeval` (4 dimensions every 10 min) |

### F. Gate and guard
| # | Pattern | In skill? | Exemplars |
|---|---|---|---|
| 23 | Approve or block an agent action by risk | yes (action-gate) | `/builds/jev-blocks-transfer`, `/builds/pi-heed` (each side-effecting tool call checked against the user's ask) |
| 24 | Moderation, spam and phishing, with an asymmetric act-only-when-sure threshold | yes | `/builds/jevmod`, `/builds/jev-antispam-bot` |

### G. Verify output
| # | Pattern | In skill? | Exemplars |
|---|---|---|---|
| 25 | Check generated code against its spec | partly | `/builds/jev-spec` |
| 26 | Check claims against evidence records | yes (citation-support) | `/community/antigravitysoham-eng-esg-disclosure-desk`, `/builds/jev-reviewer` (answers carry verbatim quotes) |
| 27 | Lint text against your own rulesets | yes | `/builds/slop-grader-cli` |
| 28 | Classifier on both sides of an LLM step: where to look before, test gaps after | **NEW** | `/builds/ticket-triage-jev-pattern` |

### H. Score, rank and match
| # | Pattern | In skill? | Exemplars |
|---|---|---|---|
| 29 | Rubric scoring of segments or items | yes | `/builds/jev-educational-video-scores`, `/builds/exam-question-predictor` |
| 30 | Verdict app with 3 outcomes (kill / fix / ship) | yes | `/builds/killmyidea` |
| 31 | Match an item to supplied candidates (help article, job, redirect target, link target) | yes | `/builds/mac-app-support-answers`, `/builds/job-match-prediction`, `/builds/smart-404-page`, `/builds/internal-link-tool` |
| 32 | Simulated panel: a persona set votes between variants | **NEW, risky** | `/builds/persona-ab-test` (label it as a simulation, never as user research) |

### I. Trading
| # | Pattern | Exemplars |
|---|---|---|
| 33 | Market call on a fixed clock | `/builds/clawby-four-dimension-reeval` |

Twelve projects sit in this pile, but author results are unverified. Keep any skill guidance to "decision on supplied evidence" and route through the financial-claims rule for human review.

### J. Meta: improving the classifier itself
| # | Pattern | In skill? | Exemplars |
|---|---|---|---|
| 34 | Calibrate your questions against your own labels, then confirm on held-out data | **NEW** | `/builds/jev-calibrate`, `/builds/jeval-confidence` |
| 35 | Know where the classifier breaks | reference | `/builds/jev-capability-atlas` |

## Suggested classifier-skill edits

1. **`jev-work.md` Always table.** Add these rows:
   - real-time control loop (#20)
   - context/memory selection (#15–17)
   - guided traversal (#19)
   - query operator (#10)
   - wake gate (#13)
   - fill a column (#5)
   - code proposes, classifier picks (#21)
2. **`jev-work.md` Split table.** Add these rows:
   - cascade to an LLM (#14)
   - classifier labels, LLM audits a sample (#3)
   - group in code, judge one representative per group (#7)
   - before and after an LLM step (#28)
3. **Recipe candidates for `recipes.md`:**
   - `context-select`: keep / drop / load per memory, file or skill
   - `control-step`: phase choice, then a conditional action question, with a latency budget
   - `calibrate`: labeled sample, threshold sweep, held-out confirmation
4. **Lessons from this run to write into the skill:**
   - **Content, not format.** Do not ask about the *kind or format* of the input. An `input` facet collapsed twice: to `structured_record` when the card was JSON, and to `short_text` when it was prose. Jev judged the card's own format, not the data it described.
   - **Pick the right "unknown" answer.** Do not fold "the card doesn't say" into a `noul` false criterion. Doing so biased the answers toward false; give such cases a separate `insufficient_context` choice instead.
   - **Report review flags per question.** With 9 questions, the batch `flagged` count (738/759) is useless. `jev_decide.py` stderr should print review counts per question.
   - **Thin cards give `insufficient_context`.** 113 cards had under 150 characters of text. Enrich cards before sorting, or budget for a large leftover pile.
   - **Reruns work as a stability check.** One reworded rerun cost $0.075 and confirmed the pile structure (476/478 unchanged).

## Data

In `data/`: `cards2.jsonl` (the cards sent), `template2.json` (the request), `results2.jsonl` (full probabilities), `exemplars.json` (top 6 per pile). Crawl sources: `browser-projects.jsonl` and `source-projects.jsonl`.

Handling: spot-check. The sort is stable across reruns, but the patterns come from summaries of author claims. Before the new rows ship, read the exemplar pages for the NEW patterns.

## Source 2: RoboNuggets "Jev use cases that blow minds" guide (PDF, read 2026-10-04)

19 use cases, each a prompt to send to an agent. Mapped against the list above and `references/recipes.md`. Of the 19, 17 are already covered. One is a new pattern, and one is a provider lead.

| # | Guide use case | Maps to | Status |
|---|---|---|---|
| 01 | Fill a Sheets column from a header + allowed options | #5 | covered |
| 02 | Triage inquiries, < 60% sure goes to a person | #14, issue-route | covered |
| 03 | Tag competitor ads from Meta Ad Library: format, CTA, funnel stage | #2 multi-question | covered; **agency fit** (ads swipe file) |
| 04 | Score 411 candidate clips from a 70-min video, keep top 10 | #29 + context-select top-k | covered |
| 05 | Churn score per customer into 3 bands | H, score | covered |
| 06 | Internal links on a site and in a second-brain vault | link-target, #31 | covered |
| 07 | Scrape comments (Apify), tag Ready to buy / Question / Complaint / Other | #1, #9 | covered; **agency fit** (social lead intent) |
| 08 | Validation set: tune on half, report on half, accuracy per confidence level | calibrate | covered |
| 09 | Pick the skill, with a None option | #12, #16 | covered |
| 10 | `/jev` picks Haiku / Sonnet / Opus per message | model-choice | covered |
| 11 | Email gate before the drafting agent (reply? brand deal? scam?) | #6 | covered |
| 12 | Chrome extension folds AI-slop posts at > 80% | #9, #24 asymmetric threshold | covered |
| 13 | Semantic Ctrl-F: pick the paragraph that matches | #10, #31 | covered |
| 14 | Image search: caption with a cheap vision model, then classify | jev-work "describe images as text" | covered |
| 15 | Live meeting: each sentence becomes a decision, action, risk, or question | control-step live-text variant | covered |
| 16 | Zero-LLM chatbot: pick the video and chapter, no generated answer | catalog-lookup, #31 | covered |
| 17 | App feature: map typed text to one of 40 icons, plus None | catalog-lookup | covered |
| 18 | Page assembles itself: pick 4 sections from a library per visitor | — | **NEW** (#36 below) |
| 19 | Mine past sessions for Jev-shaped steps | J, `context/usage-jev-candidates-*` | covered |
| — | OpenAI "Decisions API" (DevDay, ~150 ms, pick from allowed answers) | `references/providers.md` | **lead**: verify before adding as a provider |

### K. Assemble from parts
| # | Pattern | In skill? | Exemplars |
|---|---|---|---|
| 36 | Personalize by picking k of N prebuilt parts per visitor or request, then ordering them in code | **NEW** | guide #18 (Chris Tate on X; Matthew Berman video at 7:49) |

Shape: one `choice` (or `noul`) per slot over a code-owned part library, or a `relevance` score per part followed by a top-k sort in code (context-select top-k). Ordering and layout rules stay in code. The classifier only chooses which parts to show.

## Inbox

Add new ideas here as one line each: the idea, the source, and the date. On the next pass, map each line against the list above (as done for Source 2).

-
