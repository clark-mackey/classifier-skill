# Calling the classifier from a pipeline

A guide for anyone wiring classifier-skill into their own workflow (an agency's weekly ads pass, a support queue, a content audit). The interface itself is in [references/callers.md](../references/callers.md); this page is about how to use it well. Lessons come from backtests against a human's blind labels.

## 1. Ask what the item is, not what to do with it

The biggest single gain came from changing the question. Asked to pick an action for each search term ("add as keyword, move to another ad group, new ad group, ignore, exclude"), the classifier agreed with the human 38% of the time. Asked only which of the business's offerings the term was about, with code turning that answer into the action, it agreed 84% of the time, and so did the working model.

The action usually depends on facts about the caller's world (what exists, who owns it, where it lives). Neither the classifier nor a human labeler can judge those reliably from the item alone. Put the facts in a catalog, ask which catalog entry the item is about, and let code decide. This is the `catalog-lookup` recipe in [references/recipes.md](../references/recipes.md).

## 2. Keep the decision in code

- **Facts first.** Counts, dates, conversions, exact name matches, and list lookups are computed by code and never asked. Only mark a fact as known when the match is unambiguous; send the rest to review.
- **Domain rules stay.** If your process already has filters and rules (performance thresholds, required checks before a change), keep them in code and add the classifier as one more input, not a replacement.
- **Answers are evidence, not permission.** A disposition never applies a change. Your own approval process does.

## 3. Build the catalog from sources of record

Generate it with code (a sitemap, a product feed, an account export), then have a person confirm it. Group near-duplicates that lead to the same action (several pages for one product) before shipping; near-duplicates were the largest remaining error source. Rebuild when the sources change and review the difference.

## 4. Keep the pieces apart

| Piece | Owner | Holds |
|---|---|---|
| The engine (`classify_items.py`) | this skill | cards, calls, validation, dispositions, summary |
| Generic recipes (`recipes/<name>@<N>.json`) | this skill | question shapes with no one's domain rules |
| Question sheets | the caller | questions, card fields, thresholds, data rule |
| Per-business context and catalog | the caller, stored with that business's data | names, offerings, languages served, service area |
| Action rules | the caller's code | what each answer may change |

One generic sheet can serve many businesses: pass each business's facts with `--context FILE`.

## 5. Handle failure explicitly

Exit 0 means the summary was written, not that every item was answered. Check `complete` and the counts; send `human` and `unanswered` items to review or to your working model; never turn a failed item into an action.

## 6. Measure before trusting

- Label a sample blind, with the same question the classifier is asked, and keep labels out of `state`.
- Score with `scripts/score_labels.py`: choose thresholds on one split, report once on another.
- Fix the target before the test: precision on the answers you automate, at a coverage you set in advance. A precision target alone can be met by automating nothing.
- Test on fresh items the design did not see, and on more than one business.
- Keep a small blind random audit running after launch; models can share the same mistake, and disagreement sampling will not catch it.

## 7. Absorb change

Record the sheet, recipe, model, contract, and context versions with every result (the engine does this). Re-measure when the questions, the model, or the catalog's groupings change. Store labels as answers to the classifier's question, not as final actions, so they stay valid when the action rules change.
