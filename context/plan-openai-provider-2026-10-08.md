# Plan: direct OpenAI Decisions provider; no OpenRouter inside Codex

Date: 2026-10-08. Status: partly built (contract 1.8). ChatGPT-plan route deferred pending explicit Decisions support.

Built 2026-10-08: `openai` provider with translation both ways and the refusal state (Phase 1 steps 1, 2, 5, 6); host detection and the route chain `apikey` then `openrouter` (Luna), moving only on exhausted credit, with `CLASSIFIER_ROUTE` and `CLASSIFIER_HOST` (Phase 2, without the ChatGPT step); stricter review for uncalibrated Luna (Phase 4 step 1, as a family rule, not a table); call-log and summary fields; tests; docs. Live check: Codex route with only an OpenRouter key answered through Luna on OpenRouter.

Not built: Phase 0 (all evidence steps), ChatGPT sign-in (3A), auth command (3B), OpenAI limit caps and rate-limit header mapping (Phase 1 step 4; needs Phase 0), dated snapshot pin, Luna calibration, caller cache keys (name-generator is a sibling skill).

Built 2026-10-08 (outside the repo): `~/.codex/bin/model-worker jev` now runs `jev_decide.py` (backup `model-worker.bak-20261008`), passing `OPENROUTER_API_KEY` / `OPENAI_API_KEY` from Keychain items `openrouter-api-key` / `openai-api-key` into the child only. This is how Codex already got its OpenRouter key (Phase 0 step 1 partly answered) and is the working key-delivery path for 3B. Live: Jev from Claude, Luna on OpenRouter with `CODEX_THREAD_ID` set. `~/.claude/CLAUDE.md` and `~/.codex/AGENTS.md` updated together. No `openai-api-key` Keychain item exists yet.

## Goal

People running this skill in Codex on OpenAI (including the owner) get OpenAI's Decisions API directly by default when they have an API key. The current chain is OpenAI API key, then OpenRouter on exhausted API credit. The owner prefers ChatGPT-plan usage, but adding it requires OpenAI to explicitly document subscription use for `/v1/decisions`; until then, direct Decisions calls use separate API billing. Everyone else is unchanged. Luna is also reachable outside Codex through OpenRouter's existing endpoint.

## Facts this plan rests on

- OpenAI Decisions API: public beta, `POST https://api.openai.com/v1/decisions`, only model `gpt-6-luna`, $0.10/M input, no output charge, ZDR/HIPAA for eligible accounts. Shape differs from Jev: `input` vs `state`, questions array with `name`, `predicate` (answer `probability`) vs `noul`, `choices[{value,description}]`, `levels[{label,description}]`, answers array, probabilities as lists, extra `refusal` answer type. Undocumented: limits, error body, refusal schema, `confidence` definition, rate-limit headers, dated snapshots.
- OpenRouter serves `openai/gpt-6-luna-decisions-20261006` on its existing `/api/alpha/decisions` in Jev shape. Probe 2026-10-08 passed `answer_errors` for noul, choice, score; 165 tokens cost $0.0000165.
- Codex's own ChatGPT login is not a supported route for other tools. Codex docs: "For general OpenAI API calls, continue to use Platform API keys"; `auth.json` holds access tokens to be treated like a password. Community reports say Codex rotates the refresh token on use, so a second reader can break the user's Codex session. The skill never reads or copies Codex credentials.
- OpenAI's [Sign in with ChatGPT plan-usage docs](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference) support eligible Responses API requests. They do not document `/v1/decisions` as eligible. The [Decisions guide](https://developers.openai.com/api/docs/guides/decisions) documents an API key and usage pricing. Token acceptance alone would not prove that Decisions usage is included in a ChatGPT plan. OpenAI has not announced whether that endpoint will become eligible.
- Codex: default `shell_environment_policy` reportedly strips subprocess env vars whose names contain KEY, SECRET, or TOKEN (third-party source, unverified). `CODEX_THREAD_ID` is injected "when applicable" (unverified per surface). Default sandbox may block network.

## Phase 0: evidence before design (no code)

1. One live Codex run of the current skill (CLI, Desktop, `codex exec`, and a `model-worker claude` child launched from Codex). Record, without printing values: which of `OPENAI_API_KEY`, `OPENROUTER_API_KEY`, `CODEX_THREAD_ID`, `CODEX_HOME` arrive; whether network works in the sandbox; how current Codex Jev calls get their key today. Design step 3 and step 4 from this evidence.
2. ChatGPT route gate: first require official OpenAI documentation that `/v1/decisions` is eligible for ChatGPT-plan usage. Until then, do not implement Phase 3A or probe token acceptance as a substitute for billing evidence. If OpenAI documents support, run a time-boxed, throwaway spike in the session scratchpad, never the repo. Get one token with the owner's own ChatGPT sign-in, then make one tiny `POST /v1/decisions` call. Nothing from the spike ships. Record:
   - accepted or rejected;
   - plan usage per 100 and per 1,000 items (ChatGPT Settings, Usage), and whether it draws from the same allowance as Codex;
   - rate-limit headers; token lifetime and refresh behavior.
   Also confirm, from OpenAI's own pages: a published protocol spec (discovery document, registration and token endpoints, host-ID handling) or only the JS DevKit; the DevKit's license terms; which plans are eligible (Plus and Pro documented; Business, Enterprise, Edu unknown); and the eligibility category for an open-source skill run locally by other people. If Decisions rejects plan tokens, 3A stops and the API key is the only direct path.
3. Probe OpenAI limits with small calls, separately per credential type (plan token if step 2 passed, and an API key): largest accepted question count, choices per question, levels per score, input size; capture the error body and rate-limit headers for an oversize request and a 429. Store caps per credential type. Check for a dated `gpt-6-luna` snapshot.
4. List every caller and the host it runs on (pr-sweep, google-ads-tuning, name-generator, T13 search-term pipeline, model-worker jev, any other `classify_items.py` user).

## Phase 1: `openai` provider (shipped off by default in Codex)

1. Add `openai` to `PROVIDERS` in `scripts/jev_decide.py`: endpoint `https://api.openai.com/v1/decisions`, key `OPENAI_API_KEY` sent only to `api.openai.com`, explicit-only until Phase 2.
2. Translation as two pure functions at the provider boundary, `to_openai_request` and `from_openai_response`, called from the single send path after `normalize_request`, so single calls and `run_batch` both pass through them. Rules:
   - `state` to `input`; question dict to array with `name` = question id.
   - `noul` to `predicate`; its optional `true`/`false` descriptions folded into `instructions` with one fixed template.
   - `choice` criteria `{option: description}` to `choices[{value, description}]`; `score` criteria list to `levels[{label, description}]`.
   - Criteria forms richer than strings: rejected with a clear error, never flattened.
   - Answers array back to the dict keyed by id; probability lists back to dicts; `probability` back to `noul`.
   - `refusal` becomes a first-class answer state (`{"type": "refusal"}`) that `answer_errors` accepts and that maps to the `human` disposition. A refusal never makes a run incomplete; `items_out == items_in` still holds.
3. Model pinning: use a dated snapshot if Phase 0 finds one. Otherwise send `gpt-6-luna`, record the response model id on every answer and in the call log, and treat a change in that id as a recalibration and cache-invalidation trigger.
4. Limits and errors: conservative caps from Phase 0, per credential type, checked before sending; shard parallelism set from the cap of the credential in use (not Jev's 4); exponential backoff with jitter on 429; map OpenAI's error body in `failure_kind` and its rate-limit headers in `retry_after`.
5. `--local-only` refuses `openai` before anything is sent (test).
6. Docs: `providers.md` table row, contract status with check date, and the Luna-via-OpenRouter route for non-Codex use: `--model openai/gpt-6-luna-decisions-20261006`. The direct provider exists for Codex and for ZDR.

## Phase 2: provider choice and Codex behavior

1. Order: `--provider` or `CLASSIFIER_PROVIDER` always wins. Else host detection. Else today's order (OpenRouter, TypeSafe), plus `openai` when it is the only key set.
2. Host detection: use the signal Phase 0 proves reliable. A non-secret config setting (for example `CLASSIFIER_HOST=codex|other`, or a key in a small config file) overrides detection; children launched from Codex must not inherit Codex routing unless that setting says so. Every call-log entry records detected host and the reason for the provider choice.
3. The supported route chain for OpenAI work is (1) `apikey`, OpenAI direct with an API key; (2) `openrouter`, Luna through OpenRouter (`openai/gpt-6-luna-decisions-20261006`, Jev request shape). A step whose credential is absent is skipped. Host detection decides only whether this chain is used automatically (in Codex) or only when chosen (elsewhere). Add `chatgpt` ahead of `apikey` only if the Phase 0 documentation gate passes.
4. Starting point: `CLASSIFIER_ROUTE=apikey|openrouter` lets the person start lower in the chain. Default `apikey`; reserve `chatgpt` for a documented and implemented plan route.
5. Moving down the chain, automatically, only on exhaustion: API-key quota or billing exhausted (`insufficient_quota`) moves to `openrouter`. Ordinary errors, rate limits (429 with retry), and outages retry or fail on the current step; they never move down. Each move is logged with the reason and printed once in the run summary. ChatGPT-plan exhaustion handling applies only if that route becomes supported.
6. If ChatGPT-plan usage for Decisions becomes supported, define missing or broken sign-in behavior before enabling it. A missing sign-in, failed refresh, or ineligible plan is not exhaustion.
7. If no step in the chain has a credential: stop with a message naming each option; callers record `skipped`. A local model only when asked for. The call log records route, model, and credential type per call, never the credential.
8. If ChatGPT-plan usage for Decisions becomes supported, keep it opt-in outside Codex so signing in does not silently move Claude Code callers off Jev.
9. Rollout order: Phase 1 and docs ship first; the Codex rule turns on only after the caller list from Phase 0 is checked and the stop message is in place.
10. Phase 0 step 1 must show whether `OPENROUTER_API_KEY` reaches the skill inside Codex; if Codex strips it, the `openrouter` step uses the same delivery as 3B (Codex-native allowance, else an auth command without KEY in its name).

## Phase 3: credentials inside Codex

### 3A. ChatGPT sign-in (deferred; only if OpenAI documents Decisions plan usage and the Phase 0 spike passes)

1. Own module `scripts/openai_auth.py` with a small interface: `bearer()`, `login()`, `logout()`, `status()`. `jev_decide.py` only calls `bearer()`. Run as `python3 scripts/openai_auth.py login|logout|status`.
2. Protocol: built only from a published spec found in Phase 0. If only the JS DevKit exists, call it through Node as a declared dependency, or drop 3A; never hand-port it. Flow: run once by the person outside the sandbox; localhost callback on `127.0.0.1`, PKCE, state and nonce checks, ID-token verification (signature, issuer, audience, nonce). The per-install host ID is stored beside the tokens.
3. Token storage: separate from Codex. macOS Keychain through the `security` tool (argument list, no shell); on other systems the OS store when available, else a file in a user config directory with mode 0600. Never in the repo, the call log, or `naming-run/`.
4. Refresh, per account across all runs: one lock file per account next to the token store. After taking the lock, re-read the stored token and skip the refresh if another process already did it. Lock has a timeout and stale-lock detection (holder process gone). Shards and separate runs wait for the refreshed token rather than refreshing themselves.
5. Calls: bearer token only to `api.openai.com`; host check as for keys. A 401 after one refresh stops with "run `openai_auth.py login` again". Plan-usage exhaustion is its own failure kind that moves the run down the chain per Phase 2 step 5. An ineligible plan (for example Business or Enterprise, if Phase 0 shows they are excluded) gets its own message pointing to the API-key route.
6. Plan budget: before a run, estimate plan usage from item count and the Phase 0 per-1,000 figure. Warn above a per-run limit; stop above a hard cap. Both limits configurable; defaults set from Phase 0 so one classifier run cannot use up the allowance the person's Codex work relies on.
7. `logout` revokes the sign-in at OpenAI (when the protocol offers revocation), then deletes the local tokens and host ID.
8. Docs (SKILL.md and `providers.md`):
   - What the token can do: spend ChatGPT plan usage on `api.openai.com`; no access to conversations. A subprocess the agent runs can read it, like any stored credential.
   - Plan usage per 1,000 items, shared with Codex; how to see it (ChatGPT Settings, Usage); the per-run limits.
   - Eligible plans; how to sign out and revoke.
   - Codex notes: the person runs sign-in themselves (browser and localhost listener); classifier calls need network escalation; no OpenRouter.

### 3B. API key (fallback for people without an eligible ChatGPT plan)

1. Prefer a Codex-native per-variable allowance for `OPENAI_API_KEY` if Phase 0 shows one exists that does not disable the filter for every secret.
2. Fallback only if none exists: `CLASSIFIER_OPENAI_AUTH_CMD` (name avoids KEY, SECRET, TOKEN so Codex does not strip it), or the command path read from a non-secret config file. Run as an argument list, never through a shell; never log or echo its output. `providers.md` states plainly that the agent can read a key this command prints.

### 3C. Never

- Read, copy, or refresh `~/.codex/auth.json` or Codex's keyring entry.

## Phase 4: model separation and calibration

1. One threshold table keyed by model family (Jev, Luna, local), used by `review_flags` and recipe thresholds. Luna starts marked uncalibrated with a stricter human-review rule.
2. Run the `calibrate` recipe for Luna on the labeled samples (search-intent, card-sort, pr-triage). Set Luna thresholds from the results; lift the uncalibrated mark only then.
3. Caches (`classify_items.py`, name-generator `naming-run/step4-classifier/cache.jsonl`, other caller caches): key on requested pinned model plus normalized card; store the response model id beside each answer for drift detection.

## Phase 5: tests

- Translation both directions per type, including folded noul descriptions and rejected rich criteria.
- Recorded OpenAI fixtures per type plus a refusal, single call and in a batch.
- Provider-choice matrix: explicit override, Codex with and without OpenAI key, Codex child process, non-Codex.
- No OpenRouter call inside Codex under any automatic path.
- Key sent only to `api.openai.com`; `--local-only` refuses `openai`.
- Auth-command variable runs without a shell and is never logged.
- Sign-in: state, nonce, and PKCE mismatch rejected; ID token with bad signature, wrong issuer, or wrong audience rejected; token store written with owner-only permissions; tokens never appear in logs or error messages.
- Refresh: parallel shards and two separate processes produce exactly one refresh and all end with the new token; a holder that crashes mid-refresh leaves a stale lock that the next process detects and recovers.
- Credential order (inside and outside Codex): sign-in beats API key; expired sign-in with a working refresh refreshes; failed refresh stops with the login message, never falls to OpenRouter.
- Route chain: `insufficient_quota` moves from `apikey` to `openrouter`; absent credentials skip a step; 429, outages, and other errors never move down; each move logged and shown in the summary. Test plan exhaustion only if Phase 3A becomes supported.
- `CLASSIFIER_ROUTE` starts at `apikey` or `openrouter`; test sign-in behavior only if Phase 3A becomes supported.
- A run that moves mid-way keeps one model family; answers record their route.
- If Phase 3A becomes supported, an ineligible plan gets its own message and plan-budget caps are tested.
- Shard parallelism follows the credential's cap; 429 backs off with jitter.

## Phase 6: release and outside-repo follow-ups

1. Bump caller contract to 1.7 (major stays 1); changelog notes Codex routing and the refusal state.
2. `~/.codex/bin/model-worker jev`: delegate to `jev_decide.py` instead of its own HTTP call, so provider choice lives in one place.
3. Update the Jev line in `~/.codex/AGENTS.md` and `~/.claude/CLAUDE.md` together.

## Decisions recorded

- 2026-10-08: owner prefers ChatGPT-plan usage, contingent on OpenAI explicitly documenting `/v1/decisions` eligibility. The skill never reuses Codex's login.
- 2026-10-08: current route uses an OpenAI API key, then OpenRouter when API credit is exhausted. A supported ChatGPT-plan route would precede the API key.

## Open for the owner

- If Phase 3A becomes supported, decide whether a missing or broken ChatGPT sign-in stops with a fix message or moves to the API-key route.

## Open risk

OpenAI documents ChatGPT-plan use for eligible Responses API requests, not Decisions. Keep Phase 3A deferred until OpenAI explicitly documents `/v1/decisions` eligibility; a successful bearer-token request alone is insufficient. Direct Decisions calls require separate API billing in the meantime.
