# Re-tuning a recipe

Load when the call log or an eval run suggests a recipe's questions, criteria, or levels should change. Re-tuning is a person's decision; this file is the protocol for proposing one change.

1. **Evidence.** Run `python3 <skill-directory>/scripts/reshape_report.py` and quote the rows that motivate the change: recipe, calls, items, flag rate, invalid answers, failures, and the log's date range. The log holds metadata only, so name the eval cases or labeled examples that show the problem; never paste `state` or answers into the proposal.
2. **Duplicate check.** Search the recipe's history (`git log -p -- references/recipes.md recipes/`) and the changelog in [callers.md](callers.md) for the same change made or reverted before. If it was reverted, say why it would hold now.
3. **One change.** Change one recipe, and one question or criterion in it, per proposal. A generic recipe in `recipes/<name>@<N>.json` gets a new `@N+1` file; never edit a published version in place, since sheets pin it.
4. **Re-run only what changed.** Run that recipe's eval cases (`evals/run_evals.py ITERATION --cases <ids>`) and, when labels exist, the `calibrate` recipe on its held-out items. On a second round, send only the changed recipe and the items flagged last round, and report only the delta: cases that flipped, and flag-rate and accuracy change against the previous round.
5. **Keep or revert.** Keep the change only when every re-run case still passes. Record it with one changelog line: date, recipe, what changed, the evidence rows, and the cases re-run.
