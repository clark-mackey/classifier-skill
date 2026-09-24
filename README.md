# classifier-skill

An agent skill for asking a non-generative classifier model for a typed answer: pick one option, say yes or no, or place something on a scale. The default classifier is TypeSafe Jev, reached through OpenRouter or TypeSafe's own API. It returns probabilities, not prose, so answers are cheap (about $0.00002 each), quick, and easy to act on in code.

The skill works in any skills-compatible agent and with any working model. It needs Python 3 and network access to OpenRouter or TypeSafe; nothing in it depends on a particular harness.

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
- An API key for at least one route to Jev: OpenRouter (`OPENROUTER_API_KEY`, alpha Decisions endpoint) or TypeSafe directly (`TYPESAFE_API_KEY`, from console.typesafe.ai). Both can be set; the skill uses `--provider`, then `CLASSIFIER_PROVIDER`, then whichever key exists, OpenRouter first.

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

## Notes

The OpenRouter Decisions endpoint is alpha; TypeSafe's System One API is the direct route with the same request shape. Returned probabilities describe the classifier's preference among the supplied options. They are not success rates. Data you say must stay on your machine is never sent.

## Distribution

This repository is the canonical source. `The-Build-Loop/classifier-skill` is a generated mirror, not a second authoring source; do not edit it directly. It mirrors only what is pushed to this repository's `main` on GitHub, never local commits or files. After pushing here, run `python3 scripts/sync_the_build_loop.py` to publish it, or add `--check` to report drift without pushing. The publisher keeps any downstream history and adds a commit carrying the canonical tree on top of it.

- [MIT License](LICENSE)
