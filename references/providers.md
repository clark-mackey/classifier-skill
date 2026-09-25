# Providers

Load when switching away from the default Jev provider, connecting a self-hosted or Hugging Face model, or handling data that must stay on this machine.

The script sends one request shape and validates one answer shape, whoever answers. A provider is only a place that speaks that shape. Three are built in:

| Provider | Endpoint | Key | Model | Chosen when |
|---|---|---|---|---|
| `openrouter` | `https://openrouter.ai/api/alpha/decisions` | `OPENROUTER_API_KEY` | pinned `typesafe/jev-1.13` | default when its key is set |
| `typesafe` | `https://api.typesafe.ai/v1/systemone` | `TYPESAFE_API_KEY` | pinned `jev-1.13.0` | its key is set and OpenRouter's is not |
| `compatible` | `CLASSIFIER_COMPATIBLE_URL` (full request URL) | `CLASSIFIER_COMPATIBLE_KEY`, optional | `CLASSIFIER_COMPATIBLE_MODEL` or `--model` | only `--provider compatible` or `CLASSIFIER_PROVIDER=compatible` |

`compatible` is never chosen automatically, so setting its variables does not redirect existing work. Its key goes only to the configured URL's host. It accepts `https://` anywhere and plain `http://` only on `localhost`, `127.0.0.1`, or `::1`.

## What a compatible server must do

- Accept `POST <url>` with `Content-Type: application/json` and, when a key is configured, `Authorization: Bearer <key>`.
- Read a body of exactly `{"model": ..., "state": ..., "questions": {...}}`, with question types `choice`, `noul`, and `score` as in SKILL.md.
- Return JSON with `answers` (one entry per question id: `choice` with `choice`, `probabilities`, `confidence`; `noul` with `noul`; `score` with `score`, `confidence`, `probabilities`), plus `model` and optionally `usage`.

The script checks every answer (a real option, probabilities that add up, a score inside the rubric) and exits 3 on anything else, so a server that is only nearly compatible fails loudly instead of being trusted. Test a new server with one small request before a batch.

## Where a compatible server comes from

These are shapes to expect, not software this skill ships or has tested.

- **A decision model on this machine.** Open decision models (Laya, OpenJev/Verdict) score every option in one pass instead of generating text. They need their own runtime exposing the shape above, usually a small HTTP server on a local port (projects such as `laya-server` describe themselves as Jev-compatible). Set `CLASSIFIER_COMPATIBLE_URL=http://localhost:<port>/<path>`, usually with no key.
- **A decision model hosted on Hugging Face.** A dedicated Inference Endpoint running a Jev-compatible server container gives an `https://…` URL; the key is the Hugging Face access token. Hugging Face's pay-per-call serverless inference serves standard tasks, not this shape, so it will not work directly.
- **Another hosted Jev-compatible API.** Any `https://` URL that speaks the shape, with that service's key.
- **Chat models in Ollama, LM Studio, llama.cpp, or vLLM.** These serve chat or completions APIs (text out), not this shape, so the script cannot call them directly. They need an adapter server in between that turns each option into a probability, typically from token log-probabilities, and serves the shape above; point `compatible` at the adapter. Probabilities built this way are usually less calibrated than a decision model's.

## Data that must stay on this machine

Local-only data may go only to a server on this machine: run with `--provider compatible --local-only`. `--local-only` refuses any endpoint that is not `localhost`, `127.0.0.1`, or `::1`, before anything is sent. A Hugging Face endpoint or any other hosted URL is a cloud service, however the model was obtained. Without a local server configured, report that the data cannot be sent and do not call.

## Before relying on another model

Answers from different models are not comparable: a threshold tuned on Jev does not carry over. Run the `calibrate` recipe on the new provider with the same labeled sample before automating anything with it, and keep the model id with the results. The call log records the provider of every call.
