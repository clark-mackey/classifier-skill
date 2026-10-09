# Backtest: Clef, Clef-flash and Jev (Phase 4), 2026-10-09

Plan: [plan-model-profiles-2026-10-09.md](plan-model-profiles-2026-10-09.md), Phase 4. Contract 1.12 (`e347f61`).

## Decision

**Clef and Clef-flash stay `uncalibrated`.** Jev stays the default. No profile change.

- Where Clef is confident, it is right: 100% on every kept item at every threshold, on both sets. But the sets are small (48 and 60 items, 24 and 30 held out), so this cannot support Clef-specific thresholds.
- Clef's confidence runs much lower than Jev's on the 8-option intent question (mean 0.52 against 0.88; Clef-flash 0.36). At the sheet's 0.7 threshold, Clef auto-answers 12 of 48 terms and Clef-flash 5, where Jev auto-answers 40. The strict review adds to that. So on many-option questions Clef sends most items to a human. That is safe, but it saves little work.
- Clef is not cheaper here. Its estimated cost was 3–4× Jev's reported cost on the same items. It was also slower: about 21–25 s per set against Jev's 13–16 s, with the same runner.
- Next evidence worth gathering: a larger labeled set (several hundred items) with a 3–5-option question, where Clef's confidence was much higher than on the 8-option set. Re-score before changing any threshold.

## Setup

- Arms, all with explicit `--provider`:
  - `jev`: `openrouter`, reply model `typesafe/jev-1.13-20260917`.
  - `clef`: `cloudflare`, sheet pin `{"cloudflare": "clef"}`.
  - `clef-flash`: `cloudflare`, sheet pin `{"cloudflare": "clef-flash"}`.
- The same items, sheet questions and item order for each arm. One run each, no re-runs.
- Scoring: `score_labels.py`, overall, then `--target 0.9 --holdout 0.5 --seed 7`. The threshold is chosen on the tuning half and reported once on the held-out half.
- Sets:
  1. `evals/files/search-terms-labeled.jsonl`: 48 med-spa search terms, label `label_intent`, using the sheet `evals/files/search-terms-sheet.json` (recipe `paid-search-intent@1`, 8 options, threshold 0.7).
  2. `evals/files/labeled-tickets.csv`: 60 support tickets, label `team_label` (20 each of billing, bug and how-to). Converted to JSONL. The sheet is below.
- The runner and outputs were kept in the session scratchpad and are not in the repo. Keys came from Keychain and were never printed.

Tickets sheet (`eval-support-tickets@1`, `data: cloud_ok`, card field `text`):

```json
{"team": {"type": "choice", "threshold": 0.7,
  "instructions": "The item is one customer support ticket. Pick the team that should handle it.",
  "criteria": {"billing": "Charges, invoices, refunds, plans, renewals, payment methods, or account billing.",
               "bug": "Something in the product is broken, erroring, wrong, slow, or behaving unexpectedly.",
               "how-to": "The customer asks how to do something, or whether a feature exists, and nothing is broken."}}}
```

The context line is: "Support tickets for a software-as-a-service reporting product, routed to one of three teams."

## Results

Coverage / accuracy is given at each confidence threshold. "Held out" is the threshold chosen for a 0.9 target on the tuning half, then reported on the held-out half. "Auto" counts the items whose disposition was `answered` at the sheet threshold, after review.

### Search terms (48 items, 8 options)

| Arm | Accuracy | Mean confidence | ≥0.5 | ≥0.7 | Held out | Auto | Seconds | Cost (USD) |
|---|---:|---:|---|---|---|---:|---:|---|
| jev | 0.875 | 0.88 | 0.85 / 0.95 | 0.83 / 0.98 | 0.5: 1.00 / 0.96 | 40 | 13.2 | 0.00138 reported |
| clef | 0.938 | 0.52 | 0.60 / 1.00 | 0.25 / 1.00 | 0.5: 0.63 / 1.00 | 12 | 24.6 | 0.00574 estimated |
| clef-flash | 0.833 | 0.36 | 0.23 / 1.00 | 0.10 / 1.00 | 0.5: 0.17 / 1.00 | 5 | 21.0 | unknown (no price) |

Errors (label: answer):
- jev: competitor as wants_service ×2, competitor as insufficient_context, insufficient_context as researching, insufficient_context as wants_service, unrelated as wants_service.
- clef: competitor as wants_service, competitor as insufficient_context, unrelated as wants_service.
- clef-flash: competitor as unrelated ×3, competitor as wants_service, insufficient_context as researching, as unrelated and as wants_service, own_brand as researching.

Disagreements: jev and clef 3, jev and clef-flash 8, clef and clef-flash 9.

### Support tickets (60 items, 3 options)

| Arm | Accuracy | Mean confidence | ≥0.5 | ≥0.7 | Held out | Auto | Seconds | Cost (USD) |
|---|---:|---:|---|---|---|---:|---:|---|
| jev | 1.000 | 0.98 | 0.98 / 1.00 | 0.98 / 1.00 | 0.5: 1.00 / 1.00 | 59 | 15.7 | 0.00108 reported |
| clef | 0.933 | 0.81 | 0.85 / 1.00 | 0.82 / 1.00 | 0.5: 0.80 / 1.00 | 49 | 24.6 | 0.00339 estimated |
| clef-flash | 0.967 | 0.82 | 0.93 / 1.00 | 0.90 / 1.00 | 0.5: 0.97 / 1.00 | 54 | 25.2 | unknown (no price) |

Errors: clef put billing as bug ×4; clef-flash put bug as how-to ×2; jev made none. Disagreements: jev and clef 4, jev and clef-flash 2, clef and clef-flash 6.

## Caveats

- These are small, mostly synthetic eval sets that were written for this skill. Treat them as a smoke-level backtest, not a calibration.
- `score_labels.py` thresholds start at 0.5. Clef-flash's search-term confidences are mostly below that, so its held-out choice sits on the floor of the grid.
- Confidence is how concentrated the probabilities are, not the top probability. For example, a Clef answer at top probability 0.85 had confidence 0.62. Thresholds from one model never transfer to another.
- Clef's cost is an estimate from the profile price ($0.24 per million input tokens, from the docs on 2026-10-09). Cloudflare reports no cost. Clef-flash has no published price.
- One run per arm. Repeat variance was not measured.
