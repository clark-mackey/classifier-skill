# Providers

Load when switching away from the default Jev provider, connecting a self-hosted or Hugging Face model, or handling data that must stay on this machine.

The script sends one request shape and validates one answer shape, whoever answers. A provider is only a place that speaks that shape. Four are built in:

| Provider | Endpoint | Key | Model | Chosen when |
|---|---|---|---|---|
| `openrouter` | `https://openrouter.ai/api/alpha/decisions` | `OPENROUTER_API_KEY` | pinned `typesafe/jev-1.13` | default when its key is set |
| `typesafe` | `https://api.typesafe.ai/v1/systemone` | `TYPESAFE_API_KEY` | pinned `jev-1.13.0` | its key is set and OpenRouter's is not |
| `ollama` | `http://127.0.0.1:11434/v1/systemone` | none | `nimble:9b` or `--model` | only `--provider ollama` or `CLASSIFIER_PROVIDER=ollama` |
| `compatible` | `CLASSIFIER_COMPATIBLE_URL` (full request URL) | `CLASSIFIER_COMPATIBLE_KEY`, optional | `CLASSIFIER_COMPATIBLE_MODEL` or `--model` | only `--provider compatible` or `CLASSIFIER_PROVIDER=compatible` |

`ollama` and `compatible` are never chosen automatically, so configuring local models does not redirect hosted work. Ollama is always restricted to `localhost`, `127.0.0.1`, or `::1`; no flag can point that provider at a remote host. A compatible provider's key goes only to the configured URL's host. It accepts `https://` anywhere and plain `http://` only on loopback.

## Ollama System One

Ollama 0.35 and later exposes supported decision models through `/v1/systemone`. Install a model, then call:

```bash
ollama pull nimble:9b
python3 scripts/jev_decide.py --provider ollama --local-only <<'JSON'
{"state":"Ticket: charged twice","questions":{"refund":{"type":"noul","instructions":"Does the customer request a refund?"}}}
JSON
```

The default is `nimble:9b`; use `--model tev1:4b` or another supported decision-model tag explicitly. Ollama currently accepts supported Nimble and Tev1 GGUF models. It rejects an arbitrary imported chat model, including an imported Winnow model, even when that model can generate text through Ollama.

The wrapper enforces Ollama's 64-question, 26-option-per-choice-or-score, and 64 KiB request limits before sending. Ollama choice descriptions must be strings or `null`, and optional noul descriptions must be strings; the wrapper rejects richer System One criteria that hosted providers accept. For Nimble it also enforces an approximate 8,192-token context limit. Ollama's `confidence` is probability concentration, not correctness. Calibrate on labeled local examples before setting thresholds.

## What a compatible server must do

- Accept `POST <url>` with `Content-Type: application/json` and, when a key is configured, `Authorization: Bearer <key>`.
- Read a body of exactly `{"model": ..., "state": ..., "questions": {...}}`, with question types `choice`, `noul`, and `score` as in SKILL.md.
- Return JSON with `answers` (one entry per question id: `choice` with `choice`, `probabilities`, `confidence`; `noul` with `noul`; `score` with `score`, `confidence`, `probabilities`), plus `model` and optionally `usage`.

The script checks every answer (a real option, probabilities that add up, a score inside the rubric) and exits 3 on anything else, so a server that is only nearly compatible fails loudly instead of being trusted. Test a new server with one small request before a batch.

## Where a compatible server comes from

These are shapes to expect, not software this skill ships or has tested.

- **A decision model on this machine outside Ollama.** Open decision models such as Winnow, Laya, or OpenJev/Verdict need a runtime exposing the shape above. Set `CLASSIFIER_COMPATIBLE_URL=http://localhost:<port>/<path>`, usually with no key.
- **A decision model hosted on Hugging Face.** A dedicated Inference Endpoint running a Jev-compatible server container gives an `https://…` URL; the key is the Hugging Face access token. Hugging Face's pay-per-call serverless inference serves standard tasks, not this shape, so it will not work directly.
- **Another hosted Jev-compatible API.** Any `https://` URL that speaks the shape, with that service's key.
- **Ordinary chat models in Ollama, LM Studio, llama.cpp, or vLLM.** Chat/completions endpoints return text, not this shape. Ollama's first-class path applies only to models its System One endpoint supports; other models need an adapter that returns typed probabilities. Point `compatible` at that adapter.

## Data that must stay on this machine

Local-only data may go only to a server on this machine: use `--provider ollama` or `--provider compatible --local-only`. Ollama is loopback-only by construction; `--local-only` is still recommended as visible intent. For compatible servers, `--local-only` refuses any non-loopback endpoint before anything is sent. A Hugging Face endpoint or any other hosted URL is a cloud service, however the model was obtained. Without a local server configured, report that the data cannot be sent and do not call.

## Before relying on another model

Answers from different models are not comparable: a threshold tuned on Jev does not carry over. Run the `calibrate` recipe on the new provider with the same labeled sample before automating anything with it, and keep the model id with the results. The call log records the provider of every call.

## Contract status

The OpenRouter Decisions endpoint is alpha (contract last verified 2026-09-23; re-check when a call fails validation or every 90 days). TypeSafe's direct API shares the same request and answer shapes (per its docs, 2026-09-23; not yet exercised by this skill). Ollama System One was contract-tested from its documented response shape on 2026-09-30. If a contract changes, consult the current [TypeSafe API reference](https://docs.typesafe.ai/api), [TypeSafe agent documentation](https://docs.typesafe.ai/agent-skill), [OpenRouter Jev example](https://openrouter.ai/labs/jev/compile), or [Ollama decision-model documentation](https://ollama.com/library/nimble) before changing the wrapper.
