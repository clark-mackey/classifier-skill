# classifier-skill

An agent skill for asking a non-generative classifier model for a typed answer: pick one option, say yes or no, or place something on a scale. The default classifier is TypeSafe Jev, reached through OpenRouter or TypeSafe's own API. Inside Codex it uses OpenAI's Decisions API (GPT-6 Luna) instead. It returns probabilities, not prose, so answers are cheap (about $0.00002 each), quick, and easy to act on in code.

The skill works in any skills-compatible agent and with any working model. It needs Python 3 plus either network access to OpenRouter or TypeSafe, or Ollama 0.35+ with a supported local decision model.

> [!WARNING]
> **Hosted providers send data to a third party.** Calls through OpenRouter, TypeSafe, or OpenAI send the `state` you supply (item text, facts, summaries) to a cloud service outside your control. Their own retention and logging policies apply. The explicit `ollama` provider stays on loopback.
>
> **Do not expose hosted credentials where every classification must stay local.** An agent may load the skill on its own when a task looks like classification, and other skills can call its script directly. Keep `OPENROUTER_API_KEY` and `TYPESAFE_API_KEY` unset, or block outbound network access, in a strictly local environment.
>
> The skill refuses cloud providers when a request is marked local-only ("local only", "no cloud", "don't send it anywhere"). For defense in depth, leave hosted keys unset or block outbound network access. Use `--provider ollama` for Ollama System One, or `--provider compatible --local-only` for another local server; both refuse non-loopback destinations. See `references/providers.md`.

## What you can ask

| Ask the classifier to… | Example |
|---|---|
| Card sort anything into piles | "Use classifier-skill to card sort these 60 help articles into Billing, Account, and Product." |
| Classify search intent | "Use Jev to classify the intent of these 400 keywords." |
| Pick an internal-link target | "Ask Jev which of these five pages this paragraph should link to." |
| Check a draft against a brief | "Use Jev to check whether this draft covers each point in the brief." |
| Spot topic overlap | "Ask Jev whether these two blog posts compete for the same queries." |
| Route and prioritize SEO issues | "Use Jev to say which team owns this crawl finding and how urgent it is." |
| Judge meta descriptions | "Ask Jev whether each page's meta description fits its intent." |
| Track brand mentions in AI answers | "Use Jev to classify how these 50 AI answers mention our brand." |
| Qualify backlink prospects | "Ask Jev which of these prospect pages fit an outreach link." |
| Check that sources support claims | "Use Jev to check whether each cited source supports its claim." |
| Route tickets or leads | "Ask Jev which team should handle this inquiry." |
| Triage with routing code | "Use classifier-skill to triage these support emails and write the routing script." |
| Choose among model profiles you supply | "Use Jev to pick one of these three model profiles for this task. Don't start it." |

Common jobs have fixed templates in [references/recipes.md](references/recipes.md) so repeated runs use the same criteria. Lists run in one batch. Every answer comes with a handling level (automate, spot-check, or human review) and a flag when the classifier was unsure. The classifier judges meaning only: counts, status codes, and fetching pages stay in code, and it never writes text.

The skill triggers when a task means judging each of many items (tag, sort, filter, route, triage, check, score, rank, or dedupe a list), or when you name Jev, TypeSafe, or classifier-skill. A single quick judgment, writing, and open reasoning stay with your working model. Each call writes a metadata-only line to a local call log; `scripts/reshape_report.py` summarizes it.

## Install

Prerequisites:

- A skills-compatible agent
- Python 3 and Git
- One classifier route: an OpenRouter, TypeSafe, or OpenAI API key, or Ollama 0.35+ with a supported decision model such as `nimble:9b`. The skill uses `--provider`, then `CLASSIFIER_PROVIDER`, then (inside Codex or with `CLASSIFIER_ROUTE`) the OpenAI route, then whichever hosted key exists; local providers are never selected implicitly.

Use the agent skill installer:

```bash
npx skills add clark-mackey/classifier-skill --skill classifier-skill
```

Or clone the repository into your agent's skills directory, named `classifier-skill`:

```bash
git clone https://github.com/clark-mackey/classifier-skill <skills-directory>/classifier-skill
```

Set the key (`OPENROUTER_API_KEY` or `TYPESAFE_API_KEY`) through your shell, password manager, or secret manager. Do not paste a key into the skill files or commit it to Git. For a temporary terminal session without putting the value in shell history:

```zsh
read -s "OPENROUTER_API_KEY?OpenRouter API key: "
export OPENROUTER_API_KEY
```

In bash:

```bash
read -rsp "OpenRouter API key: " OPENROUTER_API_KEY; echo
export OPENROUTER_API_KEY
```

Start your agent fresh from that environment so it discovers the skill and receives the variable. `agents/openai.yaml` is optional Codex display metadata; other agents ignore it.

Optional: `hooks/nudge_classifier.py` is a Claude Code `PreToolUse` hook that adds a one-line suggestion to a sub-agent's prompt when that sub-agent is about to judge many items, so domain skills that delegate per-item work still get offered the classifier. `hooks/nudge_list_result.py` is its main-session partner, a `PostToolUse` hook that adds one note when a tool result returns 20 or more search terms, keywords, findings or similar rows. Both call no model and never block. Wiring and measurement are in `references/hooks.md`; neither is enabled by installing the skill.

## Notes

The OpenRouter Decisions endpoint is alpha; TypeSafe's System One API is the direct route with the same request shape. Returned probabilities describe the classifier's preference among the supplied options. They are not success rates. Data you say must stay on your machine is never sent.

## Calling it from another skill

For a list judged the same way every time, write a question sheet (data only: questions or a generic recipe from `recipes/`, the item fields to send, thresholds, the data rule) and run `scripts/classify_items.py`: items in, one stamped line per item out, plus a summary file. `scripts/score_labels.py` scores the answers against labels you hold back, choosing a threshold on one split and reporting it on another. How to wire it into a pipeline, and what the backtests taught, is in [docs/calling-from-a-pipeline.md](docs/calling-from-a-pipeline.md).

Other skills can call `scripts/jev_decide.py` or `scripts/classify_items.py` directly. The supported interface (where to find the script, `--contract-version`, exit codes, output shapes, the `caller` field) is in [references/callers.md](references/callers.md); only what it lists is supported, and its major version changes when anything a caller relies on breaks.

## Distribution

This repository is the canonical source. `The-Build-Loop/classifier-skill` is a generated mirror, not a second authoring source; do not edit it directly. It mirrors only what is pushed to this repository's `main` on GitHub, never local commits or files. After pushing here, run `python3 scripts/sync_the_build_loop.py` to publish it, or add `--check` to report drift without pushing. The publisher keeps any downstream history and adds a commit carrying the canonical tree on top of it. It leaves out the research material (`context/`, `data/`, and `jev-use-patterns.md`): the mirror gets the tree without them, as a new commit on the mirror's own history, so canonical history that holds that material is never pushed there.

- [MIT License](LICENSE)
