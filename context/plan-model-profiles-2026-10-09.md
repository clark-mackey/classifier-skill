# Plan: model profiles, then Cloudflare Clef

Date: 2026-10-09. Status: Phase 1 shipped (contract 1.10, 6d04f5d); Phase 2 shipped (contract 1.11, dc67ac9 and 6547f8f); Phase 3 shipped (contract 1.12, code-reviewed and re-reviewed); Phase 4 done (backtest-clef-2026-10-09.md: Clef stays uncalibrated). Revised after code-owl plan reviews rounds 1–3 (2026-10-09).

## Goal

Decision models change faster than the skill. Today each model's requirements live in code in `scripts/jev_decide.py`:
- the `PROVIDERS` table;
- jev-1.13's token limits;
- the Ollama and Nimble constants;
- the `provider == "openai"` branch in `check_provider_limits`;
- `UNCALIBRATED_FAMILIES` and the name matching in `model_family`;
- `LUNA_ON_OPENROUTER`.

Adding a model means editing that code.

The target is for a user to name a provider and model, plus usually a docs link. The skill handles the rest; the user supplies only the credential.
- The agent reads the docs and drafts a profile, which is judgment work.
- The script probes and enforces it, which is deterministic work.
- New code is needed only for a new wire shape.

Cloudflare Clef (`clef`, `clef-flash`) is the first model added this way.

## Decisions

1. **Profiles are data; credential bindings are not overridable.**
   - The shipped file, `profiles.json`, holds every built-in provider and model, one profile per model.
   - An optional user file, named by `CLASSIFIER_PROFILES`, may only add new profiles under a `user/` id prefix. A user profile id that collides with a shipped id is a load error.
   - A user profile may not name a shipped credential (key env var or Keychain item) unless it also keeps that credential's shipped host. Otherwise loading fails.
   - User profiles are always explicit-only: chosen by `--provider`/`CLASSIFIER_PROVIDER`, never auto-picked.
2. **Shapes stay code.** A small, fixed set of adapters translates requests and answers:
   - `system-one`: Jev, Ollama, compatible;
   - `openai`: Decisions;
   - `system-one+envelope`: Cloudflare v4 `{result, success, errors}`, named only after the Phase 3 live check confirms `result` is the System One body.

   A shape also fixes the outgoing field allowlist and which description types are allowed (OpenAI is text-only), so profiles carry no `fields` or `descriptions` keys.
3. **Routing stays code and points at profiles.**
   - Automatic choice uses a fixed code list (`openrouter`, then `typesafe`). It never iterates over merged profiles.
   - `ROUTES` stays in code as an ordered list of profile ids: `openai/gpt-6-luna`, then `openrouter/gpt-6-luna`.
4. **An unknown model is just `compatible` with strict defaults.** There is no separate fallback concept. Strict defaults mean:
   - the smallest current limits: Nimble's tokens, 26 options, 64 questions, 64 KiB body, text only;
   - `uncalibrated`;
   - a stderr warning.
5. **Calibration comes from the requested profile, not the reply's model name.** The review rule is decided by the profile that was asked for. A reply model id that matches none of that profile's known reply ids is treated as uncalibrated (strict), never lenient.
6. **Context size comes from docs.** Probing it would cost too much. It is enforced before sending, with a safety margin when the model's tokenizer is not Jev's.
7. **Contract.**
   - 1.10 moves everything to profiles. It adds `CLASSIFIER_PROFILES`, the loader's errors, and three call-log fields, and it makes one deliberate tightening (decision 5).
   - 1.11 adds `--probe`, with `--options` and `--dry-run`.
   - 1.12 adds Clef and `cost_estimated`.

   callers.md states the rule: a minor version adds features, or tightens validation or review with each tightening named in the changelog, and a sheet pinned to an earlier minor version keeps running.

## Profile schema (draft)

Each profile is one model. Example:

```json
{
  "cloudflare/clef": {
    "provider": "cloudflare",
    "endpoint": "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/@cf/cloudflare/clef",
    "params": {"account_id": {"env": "CLOUDFLARE_ACCOUNT_ID", "pattern": "[0-9a-f]{32}"}},
    "host": "api.cloudflare.com",
    "auth": {"env": "CLOUDFLARE_AUTH_TOKEN", "keychain": "cloudflare-auth-token", "optional": false},
    "shape": "system-one+envelope",
    "model": "clef",
    "reply_models": ["clef", "@cf/cloudflare/clef"],
    "limits": {"request_tokens": 52000, "state_question_tokens": 52000, "questions": 64, "options": 26,
               "body_bytes": null},
    "inputs": ["text"],
    "calibration": "uncalibrated",
    "price": {"input_per_m": 0.24, "source": "developers.cloudflare.com/workers-ai/models/clef", "as_of": "2026-10-08"},
    "explicit": true,
    "notes": "Docs: 65,536-token context, long state truncated silently, so the limits keep about a 20% margin"
  }
}
```

`cloudflare/clef-flash` is a separate profile with `"model": "clef-flash"`, its own endpoint path and `reply_models`, and `"price": null`.

**Environment-configured endpoints.** These keep today's behavior:
- `compatible` uses `endpoint_env` (`CLASSIFIER_COMPATIBLE_URL`) and `model_env` (`CLASSIFIER_COMPATIBLE_MODEL`). Its host comes from that URL, and plain http is allowed on loopback only.
- OpenRouter's `OPENROUTER_DECISIONS_URL` override becomes `endpoint_env` on the OpenRouter profiles. It is still checked against the shipped host `openrouter.ai`.
- `loopback_only: true` stays on Ollama.

**Validation and placeholders**
- The loader validates every profile at startup. Each of these is a hard error:
  - an unknown key, or a missing required key;
  - a placeholder without a `params` entry;
  - user-file errors (rules below).
- Placeholders are filled only from declared `params`. Each value must fully match its pattern (`re.fullmatch`, so a trailing newline fails) before the URL is built.
- **User file rules:**
  - The file must be a regular file under 256 KiB with valid JSON. A symlink is resolved and must also be a regular file.
  - Ids must use the `user/` prefix, and credential bindings must follow decision 1.

**Limits**
- `limits` drive one general `check_limits(profile, payload)`: request tokens, state-plus-question tokens, questions, options, and body bytes.
- `body_bytes` stays because Ollama's 64 KiB is a real server limit.
- The token estimate keeps today's single formula. A profile does not tune it.
- `inputs` adds `images` to the outgoing fields only when listed, and only once image checks exist. Until then, `images` stays blocked.

**Calibration and cost**
- `calibration` plus `reply_models` replace `UNCALIBRATED_FAMILIES` and the name match in `model_family`.
- `price` produces `cost_estimated` (1.12). It is kept apart from reported `usage.cost`, and is null when the price is unknown.

## Workplan

### Phase 1. Move to profiles (contract 1.10)

1. **Golden snapshot first.** Before moving anything, add a test that dumps the fully resolved provider behavior to a fixture:
   - every provider's endpoint, host, key env var, default model, field allowlist, explicit flag, loopback rule and limits;
   - the automatic choice, with each key set;
   - the route chain order.

   The same test must produce an identical dump after the move.
2. Write `profiles.json` with today's providers and models at their exact current values:
   - `openrouter/jev-1.13`, `typesafe/jev-1.13.0`, `openai/gpt-6-luna`, `openrouter/gpt-6-luna`, `ollama/nimble:9b`;
   - `compatible`, configured by environment.
3. **Reproduce today's limit quirks exactly:**
   - Jev's token limits apply where they apply today.
   - Ollama's question, option and body limits apply to every Ollama model.
   - Nimble's token limit applies only when the model is a `nimble` model.
4. Add the loader and validator with the user-file rules (decision 1 and the schema rules).
5. Replace the hard-coded values with profile reads, one at a time:
   - `PROVIDERS`;
   - the token constants;
   - the `OLLAMA_*` and `NIMBLE_*` constants;
   - the per-provider branches in `check_provider_limits`.

   Then:
   - keep `select_provider`'s automatic list in code;
   - keep `ROUTES` in code, pointing at profile ids;
   - drop `LUNA_ON_OPENROUTER` in favor of the `openrouter/gpt-6-luna` profile.
6. **Update `classify_items.py`:**
   - keep both of today's sheet `model` pin forms working unchanged (`classify_items.py:92-96`, `sheet_model` at `:170`):
     - a **bare string** is an OpenRouter model id. It applies only when the provider is `openrouter`; other providers warn and use their default.
     - an **object of provider to model** (for example `{"ollama": "nimble:9b"}`): keys must be provider names, and the entry for the running provider applies.
   - resolve a pin to a profile id as `<provider>/<model>`. A pinned model with no shipped or `user/` profile still runs, using that provider's profile with the pinned model name. It gets the strict review (decision 5) and a stderr warning, never an error, so existing sheets keep running.
   - also accept a profile id as a pin, since that form is new and additive;
   - list the valid providers and profile ids in the error message.
7. **Call log:** add `profile_source` (`shipped` or `user`) and `profile_hash` (a short hash of the resolved profile), so a log line shows which definition answered.
8. **Calibration from the profile (decision 5), in its own commit after the move.**
   - `review_flags` receives the profile's calibration.
   - An unmatched reply model id is treated as strict.
   - This is the one deliberate behavior change in 1.10. Nimble, Winnow and other "other" models move from the lenient review to the strict one until they are calibrated. Name it in the changelog.
9. **Acceptance:**
   - The golden snapshot matches.
   - All 141 existing tests pass, and any test touched by step 8 changes only in the review rule.
   - New tests pin:
     - the refusal points for Jev, Nimble, an Ollama non-Nimble model, and OpenAI;
     - the TypeSafe and Ollama field allowlists;
     - the Ollama body-byte limit;
     - `compatible`'s environment URL host check and its loopback-only http rule;
     - the OpenRouter URL override's host check;
     - automatic choice with every key combination;
     - a key set on a user profile never auto-selecting it;
     - route chain order, steps skipped when their key is absent, and `CLASSIFIER_ROUTE` validation;
     - sheet pin forms:
       - a bare string, on `openrouter` and on another provider (warns and uses the default);
       - a provider object, matching and not matching the running provider;
       - a pinned model with no profile (runs with the strict review and a warning);
       - a profile-id pin;
       - an object with an unknown provider key (an error, as today).
   - Loader error tests cover:
     - an unknown key;
     - a bad placeholder, and a pattern mismatch including a trailing newline;
     - invalid JSON, an oversized file, and a symlink to a non-file;
     - a user id without `user/`, and a user id colliding with a shipped id;
     - a user profile reusing a shipped key env var with a different host.
   - A sheet pinned to contract `1.9` runs under 1.10.
   - `--contract-version` prints `1.10`.

**Phase 1 build notes (2026-10-09).** Built as planned, with these deviations, each to keep today's behavior:
- **Two levels, not one profile per model.** `scripts/profiles.json` has `providers` (transport, credentials, shape, fields, text rules, provider limits) and `models` (model id, reply ids, calibration, model limits). Credentials live only on providers, so a model entry can never redirect a key. Clef becomes one `cloudflare` provider plus two model entries.
- **`fields` and `text` stay as provider keys.** The cut assumed the shape decides them, but OpenRouter and TypeSafe share a shape with different field allowlists, and Ollama and OpenAI have different text rules.
- **Unknown models keep their provider's limits.** Decision 4's smallest-limits default would have shrunk today's `compatible` runs (Winnow-12B) from Jev's 64k/32k to Nimble's 8k. They get the strict review and a stderr warning instead.
- **`ollama/nimble` matches by family** (`nimble:9b`, `nimble:4b`), as the Nimble check did before.
- **Path placeholders (`params`) are built now** with full-match patterns, since the loader owns them; Clef is the first user.
- Step 8 (calibration from the profile) is in the same change as the move, not a separate commit; the golden snapshot covers everything except review strictness, which has its own tests.

**Phase 1 code review (Claude subagent, 2026-10-09).** No blockers. Fixed: a user provider's credential must be a shipped one on its own host or be named `CLASSIFIER_…` / `classifier-…` (a profiles file could otherwise send `GITHUB_TOKEN` to its own URL); placeholder patterns are compiled at load and every value must also be one path segment of `[A-Za-z0-9_-]`; reply ids match exactly or with a `-`, `.`, or `:` suffix; a malformed shipped `profiles.json` or a missing route profile fails with a message, not a traceback; comment keys are allowed in limits and ignored by `profile_hash`; user ids are lowercase. Documented: Nimble and an unlisted Jev reply id (`jev-1.14`) get the strict review; a user family profile can mark variants calibrated. Kept: `LUNA_ON_OPENROUTER` (tests use it).

### Phase 2. Probing

1. **`jev_decide.py --probe <profile-id>`.**
   - It sends through the same path as a real call: the endpoint check, the key-to-host rule, https, and no redirects followed.
   - It makes one tiny call: one `noul` question with a short state.
   - It reports:
     - the reply wrapper, if any;
     - the `model` string in the reply, and whether it is in `reply_models`;
     - which `usage` fields are present, and whether cost is reported;
     - the cost of the call;
     - the elapsed time.
   - It prints a suggested profile patch and never writes files.
   - The patch can never change `host`, `endpoint`, or `auth` for a shipped profile.
2. **`--probe <profile-id> --dry-run`.** It prints the exact request and target URL without sending.
3. **`--probe <profile-id> --options`.** An opt-in, bounded check of the option cap.
   - It makes at most three calls, at 26, 64 and 128 options, each a single tiny `choice` question.
   - For each call it raises only `limits.options` to the size under test. Every other check still applies: tokens, questions, body bytes, fields, the endpoint and key-to-host rule, https, and no redirects. The raise lasts only for that probe call and is never written back.
   - It stops at the first failure and reports the largest size that passed.
   - A size passes only if the request is accepted and every option sent comes back with a probability. A model that silently drops options therefore fails the check instead of reporting an inflated cap.
   - The basic `--probe` does not run it, because most sheets use 3–12 options and the default of 26 covers them.
   - Run it once when adding a model, and record the result and date in that profile's `limits.options`.
4. **SKILL.md step "Adding a model":**
   - Read the provider's docs, and draft a `user/` profile with a source and date for each limit.
   - Run `--probe --dry-run`, then `--probe`.
   - Show the user the draft and the probe report, then save the profile to the user file.
   - Promote it to the shipped `profiles.json` only when shipping it for everyone.
   - The user supplies the credential.
5. **Acceptance:** probe tests run against a local fake server, with these cases:
   - a plain reply, a wrapped reply, a missing model, and missing usage;
   - a server that drops options past 30 (the option probe must report 26, not 64);
   - a server that accepts everything, probed from a profile capped at 26: the fake server must receive the 64- and 128-option requests, and the probe reports 128;
   - an ordinary call (not a probe) with 27 options on a 26-option profile, which is still refused locally;
   - a redirect to another host (refused);
   - a suggested patch for a shipped profile that never includes `host`, `endpoint` or `auth`.

**Phase 2 build notes (2026-10-09).** Built as planned, as contract 1.11 because 1.10 had already shipped; Clef moves to 1.12.
- `--probe` takes a model profile id or a provider name. A family profile such as `ollama/nimble` needs a matching `--model`, unless the provider's default model matches.
- The patch only touches model entries: `reply_models`, `limits.options` with an `_options_probed` date, or a new `user/` model entry for a model without a profile.
- Probes are logged as `mode: probe`.
- The fallback profile (step 1) already shipped in Phase 1.

**Phase 2 code review notes (2026-10-09, code-owl, Claude subagent).** Fixed:
- In `--options`, only a reply that drops options or a refused request (HTTP 400, 413, 422; `CallError.status`) marks the cap. Credit, rate, server, network and redirect failures are marked `inconclusive`, suggest no limit, and exit 1.
- The patch comes only from answered calls; a failed probe suggests nothing. `_options_probed` is always recorded, as `<date> with <model tag>`.
- The report and the `--local-only` refusal show the endpoint without its query string or user info; call errors pass through `redact()`.
- `--probe` with `--provider` exits 2. Docs say the patch is fields to merge.
- Seven new tests, including a real (not dry-run) OpenAI-shape probe with a mocked `urlopen`.

### Phase 3. Cloudflare Clef (contract 1.12)

**Live check results (2026-10-09, temporary `user/` profiles, account token).**
- **Models on the account:** `@cf/cloudflare/clef`, `@cf/cloudflare/clef-flash`, and `@cf/cloudflare/clef-omni` (new, not in the blog; probably the image model, unprobed). Each model has its own URL path (`.../ai/run/@cf/cloudflare/<model>`), so the path's model segment must follow the profile, not the provider.
- **Success envelope:** `{"result": {...}, "success": true, "errors": [], "messages": []}`. `result` is exactly the System One body: `model` (bare `clef` / `clef-flash`), `answers` keyed by question id, `usage`.
- **Usage:** `{"input_tokens", "output_tokens": 0}` with no cost, so `cost_estimated` = input_tokens × $0.24/M.
- **Score answers carry an extra `legend` field** (`{"0": label, ...}`); check that the response validation tolerates it.
- **Bad-auth envelope:** HTTP 401 `{"result": null, "success": false, "errors": [{"code": 10000, "message": "Authentication error"}], "messages": []}`. A wrong account id gives the same 401 code 10000, so the error text should suggest checking the account id as well as the token.
- **Latency:** clef ~0.7s, clef-flash ~0.26s end to end. A 3-question probe used 289 input tokens.
- **Still unknown:** the option cap (`--probe --options` needs the envelope adapter first), rate-limit bodies, and `clef-omni`.

1. **Live check, which gates the adapter design.**
   - Make one call per Clef model through `--probe`, using the owner's token from Keychain.
   - Record one success body, one bad-token body, and one rate-limit or allowance body if one can be triggered cheaply. Otherwise use Cloudflare's documented error codes.
   - Save the bodies without headers in the session scratchpad, not the repo.
   - Confirm three things before writing the adapter:
     - the `result` field holds exactly the System One body;
     - the reply model id;
     - the `usage` fields and the error codes.
   - If `result` is not the System One body, rename and redesign the shape before continuing.
2. **Envelope adapter.**
   - Unwrap `result` only when `success` is true.
   - Map `success: false` and the HTTP status onto the existing `failure_kind` reasons: a bad token is auth, 429 takes the rate-limit path, and a spent allowance is `exhausted`.
   - An error is never reported as `invalid_answer`.
3. **Profiles.** Add `cloudflare/clef` and `cloudflare/clef-flash` to the shipped `profiles.json` (owner decision 2026-10-09).
   - Run `--probe cloudflare/clef --options` and `--probe cloudflare/clef-flash --options` once each. Set `limits.options` from the results, and keep 26 if a probe fails at 26.
   - Token limits keep the 20% margin.
   - Both are `uncalibrated` and `explicit`.
   - `clef-flash` has `price: null` until Cloudflare publishes one.
4. **`cost_estimated`.** It is computed from reported input tokens and the profile's `price` when `usage.cost` is absent. It is logged and summarized apart from `cost`. Name it in the changelog.
5. **Codex keys.** Add the `cloudflare-auth-token` and `cloudflare-account-id` Keychain items to model-worker's key lookup in `~/.codex/bin/model-worker`.
6. **Docs.**
   - providers.md: a Cloudflare row and section, including "use a token scoped to Workers AI only" and the env var names.
   - parallel-comparison.md: a Clef arm (`--provider cloudflare/clef`).
   - callers.md: the 1.12 changelog entry.
7. **Acceptance:**
   - Fake-server tests cover:
     - success;
     - `success: false` auth;
     - 429;
     - allowance exhausted;
     - a reply model id outside `reply_models` (strict review).
   - A set Cloudflare token never auto-selects Cloudflare.
   - An account ID containing `/`, `..`, `?`, or a trailing newline is refused before any request.
   - `cost_estimated` is null for `clef-flash`.
   - One live call per model succeeds.

**Phase 3 build notes (2026-10-09).** Built as planned, with these deviations:
- **One provider, per-model URL.** Each Clef model has its own path, so provider endpoints may use a reserved `{model}` placeholder, filled from the request's model in `call_jev` (one `[A-Za-z0-9_-]` segment, else exit 1 before sending). It cannot be declared as a param.
- **Shape name** `system-one+envelope`. `unwrap_envelope` returns `result` only when `success` is true; anything else raises `CallError` ("returned no result"), so it is never `invalid_answer`.
- **Error mapping.** 401/403 keep the existing HTTP path; for a provider with URL params the message adds "check CLOUDFLARE_AUTH_TOKEN ... and CLOUDFLARE_ACCOUNT_ID". 429 with error code 3036 (daily free allocation) is `Exhausted`; other 429s retry. Code 3036 is from Cloudflare's docs, not seen live.
- **Cost.** Model profiles take `price` (dollars per million input tokens, or null). `Router` keeps `cost_estimated`, added to `log_fields`, so every call log record carries it; also in the batch stderr line, the `--probe` report and the items summary.
- **Limits.** Provider: 64 questions, `request_tokens` and `state_question_tokens` 52,000 (65,536 less 20%). Models: `options: 128` from live `--probe --options` (both passed 26/64/128; 128 is the probe's ceiling, not a known cap).
- **Not built:** images and `clef-omni`.
- **Live acceptance:** both models probed through the shipped profiles; one call through `model-worker jev` with `CLASSIFIER_PROVIDER=cloudflare`. model-worker (outside the repo, untracked) now supplies the two Cloudflare Keychain items.
- **Tests:** 12 in `Cloudflare` (mocked replies). The golden snapshot gained the `cloudflare` provider and two limits entries; nothing existing changed (additions only).

**Phase 3 code review notes (2026-10-09, code-owl, Claude subagent).** Fixed:
- An envelope failure (`success` not true) is a `CallError` with status 400, so the call log says `http_400` and `classify_items` says `bad_request` instead of `transport`. It still stops an items run, as any bad request does.
- An unusable `{model}` value raises `CallError`, not a bare exit, so a batch keeps the answers it already has.
- `model-worker` reads the Cloudflare Keychain items only when `CLASSIFIER_PROVIDER=cloudflare`.
- A reply with `"cost": null` gets an estimate. The batch line shows reported and estimated cost together. `is_exhausted` has explicit parentheses.
- Tests added for: the http_400 and bad_request labels, the estimate across several calls (with reported and null cost), an `--endpoint` override with `{model}`, a user provider with the envelope shape, and Clef's `legend` field.
- Not changed: `log_fields` carries `cost_estimated: null` for every provider (additive).
- Re-review: all fixed. Added a stderr warning when `--endpoint` drops the provider's `{model}`, since every request then goes to that URL whatever `--model` says.

### Phase 4. Backtest

1. Run Clef, Clef-flash and Jev on the same existing labeled set, with identical shards, following parallel-comparison.md. The sheet's data rule must allow cloud.
2. Score each with `score_labels.py`. Choose the threshold on one split and report it on the other.
3. Keep Clef `uncalibrated` unless the held-out results justify its own thresholds. Record the results in `context/`.

**Phase 4 result (2026-10-09).** Run on the 48 search terms and 60 support tickets in `evals/files/`. Clef was right on every item it was confident about, but its confidence runs low on many-option questions (12 of 48 search terms auto-answered at 0.7, against Jev's 40), it cost 3–4× Jev by estimate, and the sets are too small for its own thresholds. Clef and Clef-flash stay `uncalibrated`; Jev stays the default. Details: [backtest-clef-2026-10-09.md](backtest-clef-2026-10-09.md).

### Later, separate plans

- **Local clef-flash.** It needs a server that loads the Qwen backbone plus LoRA and returns typed probabilities in the System One shape. Gate it on two things: Clef winning Phase 4, and a parity check showing local quantized probabilities within a stated tolerance of hosted ones.
- **Images.** Enable `inputs: ["text","images"]` only with image checks (count, bytes, megapixels, format), and only once a task needs it.

## Risks and fixes

**Round 1 (code-owl, 2026-10-09)**

| # | Risk | Fix | Where |
|---|---|---|---|
| 1 | Account ID spliced into the URL path could change the target | Placeholders filled only from declared params that fully match their pattern | Phase 1 loader; test in Phase 3 |
| 2 | Broad Cloudflare token | providers.md: use a token scoped to Workers AI only | Phase 3 docs |
| 3 | Codex hides `*TOKEN*` variables | Keychain items in model-worker | Phase 3 |
| 4 | Envelope errors read as bad answers, so a run keeps sending after a run-wide failure | Envelope adapter maps `success: false` onto the existing failure reasons | Phase 3 |
| 5 | Silent truncation of long state | Profile limits with a margin, enforced before sending | Phases 1 and 3 |
| 6 | Limits change could alter other providers | Golden snapshot plus pinned refusal points, in its own commit | Phase 1 |
| 7 | Model id in two places, and reply-name spelling unknown | One profile per model; `reply_models`; probe records the reply name | Phases 1–3 |
| 8 | Cost estimate shown as reported cost | Separate `cost_estimated` with the price source and date; null when unknown | Phase 3 |
| 9 | Option cap unknown, or extra options silently dropped | Default 26; opt-in option probe that requires every option to come back; run once for Clef | Phases 2 and 3 |
| 10 | Backtest bias | Choose and report on separate splits; Clef stays uncalibrated until held-out results | Phase 4 |
| 11 | Local clef-flash is an unscoped build | Moved to a separate plan with gates | Later |

**Round 2 (code-owl subagent, 2026-10-09)**

| # | Sev | Risk | Fix | Where |
|---|---|---|---|---|
| 12 | BLOCKER | A user profile overriding a shipped one could send a shipped key to a new host | `user/` prefix only; collisions are errors; a shipped credential must keep its shipped host; the probe patch never touches host, endpoint or auth | Decision 1; Phases 1–2 |
| 13 | HIGH | `compatible` and the OpenRouter URL override are configured by environment, which the draft schema could not express | `endpoint_env` and `model_env`; tests for the host check and loopback http | Schema; Phase 1 |
| 14 | HIGH | Automatic choice iterates the provider table, so a user entry could be auto-picked | Fixed automatic list in code; user profiles explicit-only | Decisions 1 and 3; Phase 1 |
| 15 | HIGH | The route chain pairs a provider with a model, which the schema cannot express | `ROUTES` stays in code as profile ids; chain tests | Decision 3; Phase 1 |
| 16 | HIGH | Calibration is matched from the reply name, so an unmatched name silently gets the lenient review | Calibration from the requested profile; unmatched names strict | Decision 5; Phase 1 step 8 |
| 17 | MEDIUM | Limits are not uniform today (Nimble-only token check, global Jev limits) | Reproduce the quirks exactly; pin them, including an Ollama non-Nimble model | Phase 1 step 3 |
| 18 | MEDIUM | Sheet pins and the call log were not addressed | Pins checked against profile ids; `profile_source` and `profile_hash` in the log | Phase 1 steps 6–7 |
| 19 | MEDIUM | `cost_estimated` is a log change inside "no behavior change" | Moved to 1.12 and named in the changelog | Phase 3 |
| 20 | MEDIUM | Contract versioning was loose | The compatibility rule stated in callers.md; a test that a 1.9-pinned sheet runs | Decision 7; Phase 1 |
| 21 | MEDIUM | The envelope format was assumed before the live check | The live check gates the adapter and the shape name | Phase 3 step 1 |
| 22 | MEDIUM | The probe could bypass call checks, had no dry run and no cost report | Probe uses the real send path; `--dry-run`; per-call cost | Phase 2 |
| 23 | MEDIUM | Acceptance missed TypeSafe, `compatible`, field allowlists, body bytes, and hostile user files | Golden snapshot plus the listed tests | Phase 1 step 9 |
| 24 | NIT | A pattern ending in `$` accepts a trailing newline | `re.fullmatch`; patterns written without anchors | Schema |

**Round 3 (code-owl re-review, GPT-6 Astra via Codex, 2026-10-09)**

#12–#17 and #19–#24 confirmed fixed. #18 was not fixed until the round-3 fix below.

| # | Sev | Risk | Fix | Where |
|---|---|---|---|---|
| 25 | HIGH | The plan missed the provider-object sheet pin (`{"ollama": "nimble:9b"}`), so existing sheets could break (#18) | Both pin forms kept; pins resolve to `<provider>/<model>`; a model with no profile runs strict with a warning; tests for each form | Phase 1 steps 6 and 9 |
| 26 | MEDIUM | The option probe would be refused by its own 26-option profile limit, so it would always report 26 | The probe raises only the option limit, per call, keeping every other check; tests that 64 and 128 are actually sent, and that ordinary calls are still refused | Phase 2 steps 3 and 5 |

**Cut after review:**
- `models` map plus `family_aliases`. Each model is one profile, with `reply_models`.
- `descriptions` and `fields` profile keys. The shape defines both.
- A separate unknown-model fallback. It is `compatible` with strict defaults.
- `chars_per_token` and `non_ascii_tokens_per_char` per profile. One token formula.

**Review suggestions not taken:**
- Folding `body_bytes` into the token estimate. Ollama's 64 KiB is a real byte limit.
- Removing the option probe. It is opt-in by owner decision and runs once for Clef.

**Dropped as unnecessary (round 1 security list):**
- Scrubbing the token from logs. The call log never stores headers.
- AI Gateway retention. It only applies if the user deliberately points the skill at a Gateway URL.
- Token in shell history. Phase 3 calls go through `--probe` with Keychain, never a hand-written curl.

**Kept from existing code, no new work:**
- A key is sent only to its own host.
- HTTPS everywhere except loopback.
- Redirects are never followed across hosts.
- Ollama is loopback-only.

## Owner decisions (2026-10-09)

- Clef ships in `profiles.json` in Phase 3, without waiting for the Phase 4 backtest. It stays `uncalibrated` until Phase 4.
- The credential env var is `CLOUDFLARE_AUTH_TOKEN`, with Keychain item `cloudflare-auth-token`. There is no alias.
- `--probe --options` stays opt-in. It runs once for Clef in Phase 3, and a size passes only when every option comes back.
- All round-2 code-owl fixes accepted, with the cuts and exceptions above.

No open questions remain.
