# Providers

Load when switching away from the default Jev provider, running inside Codex, using OpenAI's Decisions API, connecting a self-hosted or Hugging Face model, or handling data that must stay on this machine.

The script sends one request shape and validates one answer shape, whoever answers. A provider is only a place that speaks that shape (OpenAI's is translated to it and back). Five are built in:

| Provider | Endpoint | Key | Model | Chosen when |
|---|---|---|---|---|
| `openrouter` | `https://openrouter.ai/api/alpha/decisions` | `OPENROUTER_API_KEY` | pinned `typesafe/jev-1.13` | default when its key is set |
| `typesafe` | `https://api.typesafe.ai/v1/systemone` | `TYPESAFE_API_KEY` | pinned `jev-1.13.0` | its key is set and OpenRouter's is not |
| `ollama` | `http://127.0.0.1:11434/v1/systemone` | none | `nimble:9b` or `--model` | only `--provider ollama` or `CLASSIFIER_PROVIDER=ollama` |
| `compatible` | `CLASSIFIER_COMPATIBLE_URL` (full request URL) | `CLASSIFIER_COMPATIBLE_KEY`, optional | `CLASSIFIER_COMPATIBLE_MODEL` or `--model` | only `--provider compatible` or `CLASSIFIER_PROVIDER=compatible` |
| `openai` | `https://api.openai.com/v1/decisions` | `OPENAI_API_KEY` | `gpt-6-luna` (no dated snapshot yet) | inside Codex, with `CLASSIFIER_ROUTE`, or `--provider openai`; never just because its key is set |

`ollama` and `compatible` are never chosen automatically, so configuring local models does not redirect hosted work. Ollama is always restricted to `localhost`, `127.0.0.1`, or `::1`; no flag can point that provider at a remote host. A compatible provider's key goes only to the configured URL's host. It accepts `https://` anywhere and plain `http://` only on loopback.

## OpenAI Decisions and the Codex route

OpenAI's Decisions API (public beta) answers with GPT-6 Luna. Its request and answer shapes differ from System One's, so the script translates both ways: `state` becomes `input` (an object or array is sent as JSON text), question ids become `name`s, `noul` becomes `predicate` (its `true`/`false` descriptions are added to the instructions), and choice options and score levels become `choices` and `levels`. Answers are translated back and checked like any other; a score level whose label is not the level asked at that position, or an option or level listed twice, makes the answer invalid. Structured instructions or descriptions are refused before sending, because Decisions takes only text. A `refusal` answer comes back as `{"type": "refusal"}` and is always flagged for a person.

Direct Decisions calls use API billing, separate from a ChatGPT subscription. OpenAI's [Decisions guide](https://developers.openai.com/api/docs/guides/decisions) documents an API key and usage pricing. [Sign in with ChatGPT plan usage](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference) currently documents eligible Responses API requests, not `/v1/decisions`.

**Route chain.** Inside Codex, or whenever `CLASSIFIER_ROUTE` is set, the script uses this chain instead of picking one provider:

1. `apikey`: OpenAI Decisions with `OPENAI_API_KEY`.
2. `openrouter`: Luna through OpenRouter (`openai/gpt-6-luna-decisions-20261006`, System One shape) with `OPENROUTER_API_KEY`.

A step without its key is skipped. The run moves down only when a step's credit runs out (OpenAI `insufficient_quota`, OpenRouter HTTP 402); rate limits, outages, and other errors retry or fail where they are. Each move is printed and recorded in the call log and the `classify_items.py` summary (`route_moves`). Every step answers with Luna, so thresholds and comparisons hold across a move; a `classify_items.py` line is stamped with the model that answered it. `CLASSIFIER_ROUTE=openrouter` starts lower. An explicit `--provider` or `CLASSIFIER_PROVIDER` always wins and uses that provider alone, so `--provider openrouter` inside Codex still means Jev. `--model` and `--endpoint` need `--provider` when the chain is in use, and `classify_items.py` ignores a sheet's `model` pin on the chain (with a warning). `--dry-run` needs no key on the chain.

A ChatGPT sign-in step for Decisions is deferred unless OpenAI explicitly documents plan usage for `/v1/decisions`; token acceptance alone would not establish subscription billing or supported use. See `context/plan-openai-provider-2026-10-08.md`. The script never reads or reuses Codex's own login (`~/.codex/auth.json`).

**Codex.** The script treats a run as inside Codex when `CLASSIFIER_HOST=codex`, or when `CODEX_THREAD_ID` is set and `CLAUDECODE` is not (a Claude worker launched from Codex keeps the normal choice). `CLASSIFIER_HOST=other` turns the Codex route off. Two Codex defaults get in the way:

- Codex hides environment variables whose names contain `KEY`, `SECRET`, or `TOKEN` from the commands it runs, so `OPENAI_API_KEY` and `OPENROUTER_API_KEY` may not arrive. Without either, the script stops with exit 1 and names the variables. Let the variable through in Codex's `shell_environment_policy` without turning the filter off for every secret.
- Codex's sandbox may block network access; ask for approval to run classifier calls outside it.

**Calibration.** Luna's review thresholds are not yet calibrated on labeled samples, so its answers get a stricter review rule: a wider close-runner-up margin (+0.1), a wider noul band (0.3–0.7), and a score-confidence bar of 0.6. Run the `calibrate` recipe on Luna before automating anything with it. The same rule applies to every model whose profile is not marked calibrated (see Profiles).

## Profiles

Every provider and model setting above lives in `scripts/profiles.json`, not in code. A **provider** holds transport and credentials: endpoint (or the environment variable that supplies it), host, key variable and Keychain item, request shape (`system-one` or `openai`), accepted fields, text-only rules, and provider-wide limits. A **model** holds what differs per model: the model id, the reply ids it may report (a prefix match, so dated snapshots count), whether its review thresholds are `calibrated`, and model limits such as a context window. Limits are `request_tokens`, `state_question_tokens`, `context_tokens`, `questions`, `options`, and `body_bytes`; defaults are jev-1.13's.

The review rule comes from the profile that was asked for. Only a calibrated profile whose reply ids match the reply's `model` gets the normal review; an uncalibrated profile, a reply id the profile does not list, or a model with no profile gets the strict rule. A model with no profile still runs on its provider's limits, and stderr says so. Today only Jev is calibrated, so Nimble and every other Ollama or `compatible` model get the strict rule. A reply id matches a listed one exactly or with a `-`, `.`, or `:` suffix (`typesafe/jev-1.13-20260917`, `nimble:9b`); a new Jev release whose reply id is not listed, such as `jev-1.14`, gets the strict rule until its profile is added.

**Your own profiles.** Set `CLASSIFIER_PROFILES` to a JSON file with `providers` and/or `models` objects, in the same format. Every id must start with `user/` and use lowercase letters, digits, and `. _ / -`, and a user file can only add: it cannot change a shipped provider or model, or repeat a shipped provider-and-model pair. A user provider's key is either a shipped key variable or Keychain item used on that key's own host, or a new one named `CLASSIFIER_…` (Keychain: `classifier-…`), so a profiles file cannot pick up an unrelated secret and send it elsewhere. User providers are never chosen automatically; name them with `--provider user/<name>`. A user model with `"match": "family"` covers variants such as `name:tag` of a model whose exact id has no profile, including a shipped provider's, and can mark them calibrated; only add one you have calibrated. An endpoint may use path placeholders such as `{account_id}`, each declared under `params` with an environment variable and a regular expression the whole value must match; every value must also be a single path segment of letters, digits, `_`, and `-`, so it cannot add path segments, a query, or a host. The file must be a regular file of at most 256 KiB; any error stops the run before anything is sent. The call log records `profile`, `profile_source` (`shipped` or `user`), and `profile_hash` for each call.

**Probing a model.** `jev_decide.py --probe <model profile id or provider>` (add `--model` for a provider, or for a family profile such as `ollama/nimble`) sends one tiny request through the normal send path, so the endpoint check, key-to-host rule, https, and no-redirect rule all apply. It prints:
- the reply's wrapper (`none`, `result` for an envelope this skill does not unwrap yet, or `unknown`);
- its model id, and whether the profile lists it;
- its usage fields and cost;
- any answer errors;
- a suggested patch.

The patch lists only the fields to merge into one model entry (reply ids, option limit), never a host, endpoint, or credential; it is empty when no call answered, and nothing is written. `--dry-run` prints the requests without sending. `--options` finds the option cap with up to three calls at 26, 64, and 128 options. It replaces only the option limit, only for those calls; a size passes only when every option comes back, so a model that silently drops options is caught. A refused request (HTTP 400, 413, 422) also marks the cap; credit, rate, server, network, or redirect failures leave it unknown, suggest no limit, and exit 1. Run it once when adding a model, and record the result in that model's `limits.options`. The patch's `_options_probed` names the date and the model tag probed; for a family profile, that one tag stands for the family.

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

The OpenRouter Decisions endpoint is alpha (contract last verified 2026-09-23; re-check when a call fails validation or every 90 days). Luna on OpenRouter's endpoint passed answer validation in a live call on 2026-10-08. OpenAI's Decisions API is a public beta, translated from its documented shapes (2026-10-08); its limits, error body, rate-limit headers, and refusal schema are undocumented and not yet exercised by this skill, so its first live calls should be small. See the [OpenAI Decisions guide](https://developers.openai.com/api/docs/guides/decisions). TypeSafe's direct API shares the same request and answer shapes (per its docs, 2026-09-23; not yet exercised by this skill). Ollama System One was contract-tested from its documented response shape on 2026-09-30. If a contract changes, consult the current [TypeSafe API reference](https://docs.typesafe.ai/api), [TypeSafe agent documentation](https://docs.typesafe.ai/agent-skill), [OpenRouter Jev example](https://openrouter.ai/labs/jev/compile), or [Ollama decision-model documentation](https://ollama.com/library/nimble) before changing the wrapper.
