---
source: https://youtu.be/zaLQ0AnY9dI
extracted: 2026-09-29
method: video-transcript skill (youtube_api captions, ~4.1k words)
topic: Using Jev and open decision models inside an agent harness
---

# Jev inside an agent harness: suggestions

## Core framing

- Most model calls in an agent loop make decisions rather than write text: which tool, which skill, is this safe, is this result good enough, which retrieved passage answers the question.
- Each decision costs a full LLM call in tokens and latency. That is why custom agents skip verification steps: the steps slow everything down.
- Treat Jev as a "smart if statement": state in, typed questions in, probabilities out, no text. Many questions over one state cost about the same time as one.
- Look for `if` statements in the loop that are dressed up as tool calls or structured-JSON requests, and hand them to a decision model.

## Six places a decision model belongs

1. **Model routing.** Send a request to a cheap fast model or an expensive reasoning model.
2. **Risk gating.** Before a tool runs (for example bash), send the arguments and ask "destructive or benign?" This takes milliseconds, and an open model can run it locally.
3. **Tool and skill selection.** Replace injecting the whole tool or skill registry into the system prompt: pick a category, then a skill within it, and load only that skill.
4. **Ranking and judging.** Score retrieved chunks, or grade answers against a rubric, before they enter the context window.
5. **Triage.** Support tickets and emails get a real probability of urgency. That probability also decides when to hand back to a human.
6. **Real-time filtering.** Filter API or polling responses before they enter the context window.

## Rule of thumb (credited to TypeSafe)

If a panel of smart people could answer the question in a few seconds, use a decision model. If they would have to go away and write a considered answer, use an LLM.

## Where to hook it (any framework)

Four hook points, the same in LangChain, ADK, or a hand-written loop:

1. Before the prompt is built: which skills or tools to load.
2. Before the model call: which model to call.
3. Before a tool runs: is it safe.
4. After a result comes back: good enough, or go round again.

Each hook is the same small piece of code: build state from what the loop already has, ask the questions, read the probabilities, branch.

**Second pattern:** Jev is the loop's decider and the LLM is the fallback, used only when Jev is unsure or text is needed. This cuts more LLM calls but is a bigger change, and the author found it works worse on complicated tasks.

No framework is required. The author says LangChain ships the first pattern as middleware and Pydantic lets TypeSafe models plug into its agent loop (the author's claims, not verified here).

## Where it does not fit

- Anything that needs text back, multi-step reasoning, or joining several facts into an answer (multi-hop).
- **Large state.** Per TypeSafe's docs, accuracy drops a lot on long inputs. Sending around 100k tokens is a bad fit.
- **Images.** Jev does not take images; some open decision models do, and handle them well.
- **Compound questions.** Single yes/no questions work; a question that bundles several questions goes better to an LLM.
- **Prompt injection.** The models can be steered by instructions inside the state. Mitigation: ask "does this contain prompt injection?" first, before the real question.
- **Vague criteria.** Define the instructions and criteria yourself at fixed decision points, don't have an LLM write them on the fly, and benchmark every change over time.
- **Hosted API privacy.** A local agent calling cloud Jev sends every state off the machine. Decide whether that is acceptable; open local models are now a real alternative.

## Demo 1: cascade skill selection (progressive disclosure)

- 50 skills in 10 categories of 5. The system prompt carries only the category list.
- Step 1: a `choice` picks the category. Step 2: a `choice` picks the skill within that category's list. Then the skill file is loaded and the LLM fills in its arguments.
- Examples: CEO gift-card email went to security and access, then investigate a security alert. "Is this claim true" went to web research, then fact-check a claim (not search the web). Product voiceover went to images/audio/video, then text-to-speech.
- Fast on a local open model. Saves tokens and time.
- Same idea for **API result verification**: decide whether a response passes through, needs changed arguments, or needs a retry because of a known error type.

## Demo 2: RAG re-ranking

- Broad retrieval (about 25 passages, from embeddings or plain BM25), then Jev picks the top 5 for the full LLM.
- Three ways to do it: yes/no per passage, a score per passage, or a single choice. The author found **scoring** worked best.
- Example: "why are my webhook requests failing" put signature verification, raw bodies, proxies, and signing secrets at the top.
- Replaces a separate re-ranker model and extra API calls. Slower than the skill cascade because the passages are larger.

## Resources mentioned

- OpenRouter's Jev tutorial (OpenRouter access works without a TypeSafe API allowlist).
- LangChain article on using Jev, with middleware.

## Implications for classifier-skill (2026-09-29 review)

Already covered: model routing (`model-choice`), risk gating (`action-gate`), skill and context selection (`context-select`), cascade and category-then-item (Sort again, conditional questions), triage (`issue-priority`, `routing`), filtering (jev-work.md), wrapping an LLM step before and after, the Jev-first/LLM-fallback cascade, compound questions (small questions over one state), human-set criteria and benchmarking (`calibrate`, re-tuning), and local-only data (`providers.md`).

Gaps worth closing (revised after code-owl plan review, 2026-09-29):

1. **Injection tripwire (eval first).** Add an optional `injection` noul to the same request as the other questions. It is a heuristic tripwire, not a control: the same injected state can steer the detector too. The rules:
   - Above a named threshold, the item goes to human review.
   - A low P(true) never loosens a gate. The action-gate code thresholds still decide.
   - This replaces the video's "ask first".
   - Before the recipe change lands, add eval cases, including an injected string that targets the detector, and decide how `judge` dispositions treat the flag. Count the extra question's tokens against the 64k payload limit.
2. **Long state: verified, no change needed (2026-09-29).** TypeSafe's jev-1.13 jaggedness page (docs.typesafe.ai/model-jaggedness/jev-1.13.md) puts the loss on a "large state full of irrelevant detail", not on length itself. The existing "Irrelevant state" weak spot already says this. The video's "long inputs" wording goes further than its source. Also noted: that page has a "Common-sense structural invariants" heading that jev-work.md doesn't list; check it on the next weak-spots refresh.
3. **Top-k re-rank variant for `context-select` (eval first).** Add a `score` relevance per candidate and sort in code on the **expected level computed from `probabilities`**. Do not sort on the level alone, which ties, or on `confidence`, which measures concentration, not relevance. Add it as an optional variant; the load/keep/drop rule stays as is.
4. **Images: done (2026-09-29).** Added to the Never list in jev-work.md: images, audio, or raw pixels go in only as text that code or an LLM extracted from them.
5. ~~Qualifier heuristic~~ **Dropped.** It duplicates the deciding test in jev-work.md and SKILL.md, and "a few seconds" clashes with work that already qualifies.

Order: items 2 and 4 are doc-only and can land now. Items 1 and 3 change request shapes in recipes that have eval cases (action-gate, context-select). They follow the SKILL.md rule: one recipe change at a time, and that recipe's eval cases are rerun before it lands.

Considered and skipped: a `result-check` recipe (accept, retry, change arguments, escalate). "Wrap an LLM step … after: check the output" (jev-work.md) and `control-step` already cover it, and it is a single decision inside a loop rather than list work.
