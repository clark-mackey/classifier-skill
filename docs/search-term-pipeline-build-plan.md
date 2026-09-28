# Build plan: search-term triage with a classifier

Status: draft for review, not built. Written 2026-09-28; revised the same day after a code-owl plan review (GPT-6 Astra) found the first draft overbuilt for the evidence.
Scope: GTA task T13 (every search term with spend ends in one of five actions), with a cheap classifier (TypeSafe Jev, through classifier-skill) doing one judgment per term and code doing the rest. The pipeline and the classifier-facing pieces will change as results come in; section 8 says how.

Related: `docs/caller-integration-plan.md`. Account names are kept out of this file; results by account live in the client repositories.

---

## 1. Evidence

Two backtests, 2026-09-26 to 28, scored against the owner's blind labels.

| Round | Classifier asked | Action accuracy vs owner |
|---|---|---|
| Account A, v1-v2 | pick the action | Jev 49%, working model (Opus) 44% |
| Account B, v3 | pick the action, 5 options | Jev 38%, Opus 32%; the owner could not label without a list of the business's offerings |
| Account B, v4-v5 | name the offering; code picks the action from a catalog | **Jev 84%, Opus 85%**; Jev's confident exclusions 11 of 11; Jev $0.007 and 65 s per 80 terms vs ~100k tokens for Opus |

What the evidence supports: naming the offering and resolving the action in code, backed by an owner-confirmed catalog. What it does not yet support: a multi-stage split, a business taxonomy, inherited playbooks, or replacing owner review with a QA agent. 11 of 11 confident exclusions has a one-sided 95% lower bound of about 76% precision; the sample is small. The 11 v5 misses: 5 near-duplicate catalog entries, 3 policy calls (distance, other specialties, filler vs surgery), 2-3 label noise. 16% of terms were ambiguous even to the owner.

---

## 2. Decisions and why

| # | Decision | Reasoning |
|---|---|---|
| D1 | The classifier answers "which offering is this term about?" and one gate, "could this never become a customer?". Code decides the action. | Asking for the action made both the classifier and a human guess account facts; asking for the offering doubled accuracy (38% to 84%). |
| D2 | **Keep T13's doctrine intact.** Code runs the existing windows and filters (Lost Spend and Hidden Waste every time, Efficiency and Volume triage, Stick or Twist, Favourable Message-Match, Ad Rank Killers, High CTR no conversion, Really High CPC), the n-gram pass for phrase negatives, the five reasons a term earns a keyword, separate handling of converting terms, and the shared-list and `decisions/` checks before negating. | Those rules live in `gta-account-optimization` (`references/tasks.md` T13, `references/gta-doctrine.md`). The classifier adds one semantic judgment; it does not replace performance triage. The first draft dropped these and would have let relevant but wasteful terms stay. |
| D3 | **Offering groups** in an owner-confirmed catalog, built by code from the sitemap and the ads account (current serving status, account-scoped ids). Website and account entries are candidates until the owner confirms them. | Near-duplicate pages were the largest error source. A live page does not prove a current offering; the owner does. |
| D4 | **Only verified fact shortcuts.** Conversion, exact brand and principal names, the account's own competitor lists, and exact keyword coverage are decided in code. An ambiguous name, place, or language goes to review, never to a decision. | A lookup is exact only when the match is unambiguous; a place name that is also a surname is not. |
| D5 | Use the existing classifier engine (`classify_items.py`, contract 1.3) with its full failure contract: check the summary and counts, keep `human` and `unanswered` items for review, fall back to the original list. | Exit 0 does not mean every item was answered; a failed item must never become a stay or a negative. |
| D6 | **Output is a GTA change list.** The conductor keeps state, approval, apply, verification, audit, and reversal. | The ads pipeline already owns those (`google-ads-conductor`: never apply outside an approved change list). |
| D7 | **Client data lives in the client repository**: catalog, results, labels. Pipeline code and generic question sheets live in `gta-account-optimization`. | The conductor never holds account data anywhere but the client repo. (Today's prototype files in `~/.config/classifier-skill/` move there.) |
| D8 | New code uses industry-neutral names (`business`, `customer`, `offering`, `offering_group`, `principal`, `competitor`); existing files are not renamed now. | The pipeline must serve any business; renaming working files now adds churn without evidence of benefit. |
| D9 | **Success is a fresh, pre-registered benchmark**, not a rescore of the 80 labels that shaped the design. | Reusing design data rewards overfitting. |

---

## 3. The minimum version

Per weekly T13 run on one ads account:

1. **Pull** search terms for T13's windows (7 days, plus 30 days for low volume) with serving status, as the GTA skill does today.
2. **Doctrine filters (code)**: run the T13 recipes and the n-gram pass exactly as today. Each term carries its recipe flags.
3. **Facts (code)**: converted; exact brand or principal match; match against the account's own competitor and negative lists; exact coverage by the triggering ad group's keywords. Anything ambiguous is marked `unknown`.
4. **Classify (Jev)**: two questions per remaining term: `cannot_win` (yes/no) and `offering_group` (a choice over the catalog's offering groups plus `business_by_name`, `general_category`, `not_offered`, `none_fit`, `insufficient_context`). The industry's own words are used in the question text.
5. **Resolve (code)**, combining doctrine flags, facts, and answers:
   - converting terms are never negated in bulk (doctrine); performance triage decides them;
   - cannot win, or not offered, and not converted: proposed negative; the n-gram pass decides exact vs phrase; shared-list and `decisions/` checks run first;
   - offering group has an active ad group: stay if it triggered there (add keyword only for one of the five reasons, else ignore), otherwise add to that ad group (rule 2);
   - offered with no ad group: add to the new-ad-group list (rule 1), holding in a general ad group when the account says so;
   - below threshold, `unknown` facts, `none_fit`, or `insufficient_context`: review.
6. **Emit** a GTA change list grouped by rule, plus the new-ad-group list with cost per offering group. Nothing is applied without approval.

Per account, once: build the catalog (offering groups, aliases, pages, active ad groups, service area) and have the owner confirm it; repeat when the website or account structure changes, reviewing a diff.

---

## 4. What changes where

| Where | What |
|---|---|
| `gta-account-optimization` (ads repo) | T13 step 4-5 added; resolver and fact code; generic question-sheet template; catalog builder. Owner go-ahead needed. |
| Client repo, per business | `catalog.json` (confirmed), weekly results, owner labels |
| classifier-skill | nothing new; the engine and the `catalog-lookup` recipe already exist |

---

## 5. Benchmark (the success test)

Pre-register before running:
- **Data**: a fresh week of terms, at least two businesses, owner labels collected blind.
- **Arms on identical inputs**: (a) today's T13 run by the working model, (b) the minimum version.
- **Report**: per-action precision with confidence intervals, end-to-end wrong negatives, coverage (share of terms not sent to review), owner review minutes, and cost.
- **Pass**: the minimum version's proposed negatives are at least as precise as arm (a), at a coverage fixed in advance, with lower cost and no more owner time.

Wrong negatives on a live account are the costliest error; they are scored separately and gate everything else.

---

## 6. Owner labels and review

- Blind random audit of each weekly change list by the owner (a small fixed sample), in addition to reviewing low-confidence items.
- A fresh labeled sample for every new business, even in a familiar niche, until its audits hold.
- Business-specific policy overrides (e.g. which languages it serves, which offerings draw customers from far away) live in its catalog.

---

## 7. Later, only if the benchmark says so

Each item needs evidence that it raises precision at fixed coverage or cuts total review time.

| Idea | Trigger to build it |
|---|---|
| Split classification into stages ("belongs here?", then "where does it go?") | the minimum version's errors cluster in routing, not in offering identification |
| Business taxonomy (sector > industry > GBP category) and a `market` field so calibration transfers across businesses | three or more businesses in one market show consistent policy and accuracy |
| Market playbooks with inheritance and a vocabulary block | the same rules are being repeated in several catalogs |
| QA agent in place of routine owner review | it matches the owner's blind audits as well as a second human would, over several weeks |
| Service-area radius expansion and per-offering customer reach | an account with a radius-defined area or extended-reach offerings is onboarded |
| Renaming existing prototype files to the neutral vocabulary | when those files are rewritten anyway |

---

## 8. How change is absorbed

- Every result records the catalog version, question-sheet version, model, contract version, and resolver version, plus the inputs it saw.
- A change to the question sheet, the model, or the offering groups is re-benchmarked before it ships; labels tied to a catalog version are re-checked when that catalog changes.
- Only the question sheet and the call to the engine know about the classifier; swapping the model changes those two.

---

## 9. Decisions on the open questions (owner, 2026-09-28)

1. **Coverage for the benchmark: 50%.** At least half of the terms must be decided without review, with proposed negatives at least as precise as today's T13.
2. **Weekly blind audit: 20 random terms** in the labeling page, blind to the model's answers (about 5 minutes).
3. **Spanish terms route to the matching English ad group for now**, and also go on a Spanish build list with cost per offering group. Spanish ad groups linked to the Spanish pages wait until the list's spend justifies writing Spanish ads.
4. **Placement approved:** the T13 steps go into `gta-account-optimization` in the ads repo, and each business's catalog, results, and labels move into its client repo.
