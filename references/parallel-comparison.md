# Parallel decision-model comparison

Use when the user names multiple decision models, asks for a second opinion, or wants to compare quality, latency, or cost. Comparison is optional; an ordinary classification run follows the default route in `SKILL.md`.

## Prepare one shared request

1. Define one versioned request: identical `state` for each item and identical question ids, types, instructions, criteria, and item order. Keep a `(shard, line)`-to-item-id map outside the request; `--batch` emits the source `line` number within each shard, including gaps for blank input lines. Use a request that fits every selected provider: OpenAI requires string instructions and string or null descriptions; Nimble allows string or null choice descriptions, string noul descriptions, and has Ollama's 26-option, 64-question, 64 KiB, and approximate context limits in [providers.md](providers.md).
2. Set a call count and spend ceiling before dispatch. The runner has no hard spend cap. Use a small representative sample to estimate each hosted arm's cost, then send bounded shards whose conservative estimated total fits the remaining ceiling; check reported cumulative cost after every shard and stop before the next if it would exceed the ceiling. If an arm does not report cost or has no usable estimate, do not run it on the full set under a claimed ceiling.
3. Apply the strictest data boundary to the whole comparison. If the data must stay local, use only local decision models; do not send a copy to Jev or OpenAI. Do not put credentials or unnecessary client data into a subagent brief.

## Dispatch

Use one subagent per provider when the host permits parallel agents and the task authorizes their use. Give each a short brief containing the shared request path or exact input, its sole provider/model, output path, call budget, and the instruction to run that provider only. It may invoke its assigned decision endpoint; it must not delegate further or switch providers. With no subagent support, run independent commands concurrently or sequentially. Keep each provider's output and errors in separate files.

| Comparison arm | Explicit invocation |
|---|---|
| Jev on OpenRouter | `--provider openrouter --model typesafe/jev-1.13` |
| OpenAI Decisions | `--provider openai --model gpt-6-luna` |
| Local Nimble | `--provider ollama --model nimble:9b --local-only` |
| Cloudflare Clef | `--provider cloudflare --model clef` (or `--model clef-flash`) |

For a single item, pipe the same JSON request to `scripts/jev_decide.py` with each arm's flags. For several items, give each arm the same JSONL input and question template through `--batch`, using the same shard boundaries across arms. Explicit `--provider` prevents the Codex OpenAI-to-OpenRouter route from substituting Luna on OpenRouter for a direct OpenAI arm. Use credentials only for the matching provider; do not assume OpenAI Decisions is covered by a ChatGPT subscription. A missing credential, quota error, refusal, or invalid answer leaves that arm unanswered. Record it as such instead of replacing it with another model.

## Compare and decide

Use each output file's shard id and each result's `line` with the saved map to join by item id and question id; never align partial outputs by row position. Retain model id, provider, selected answer, full probability distribution, and `review` reasons; record each arm’s elapsed time and reported cost from its run summary. Report per-arm coverage, disagreement count and examples, review counts, time, and cost. If labeled examples exist, score each arm against the same labels and compare error rates by question or segment; agreement alone does not establish accuracy. Probabilities from different models are not calibrated to one scale, so do not average them or apply one model's threshold to another. Use disagreement or low confidence to select items for human review, and keep the working agent responsible for any downstream action. Never let majority vote authorize a consequential action.
