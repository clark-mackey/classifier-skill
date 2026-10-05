# browser-scout: madewithjev.com (crawled 2026-09-24/25, site says "Updated Sep 25, 2026")

## Output
- browser-projects.jsonl: 759 records = 685 /builds/ + 74 /community/. Required fields plus extra
  `_quote` (author's post text), `_summary` (site summary), `_all_categories` (every category page that lists the build).
- How it was made: the build/category DOM was inspected and all 685 build pages were parsed in the browser pane
  (in-page fetch + DOMParser). The in-browser result could not be moved to disk (a local listener was denied), so
  the same parser was ported to stdlib regex over the same SSR HTML (curl, cache in ../bs/m/). Results match the
  in-browser run exactly: 685 builds, 292 in several categories, 449 with a Jev decision clause.
- `decision`, `question_type`, `input_kind` are keyword HEURISTICS, not per-item reading. question_type is "unclear" for
  about 304 builds. `domain` = the site's primary "Use case". Treat these fields as first-pass tags. Good fodder for a Jev/LLM pass.

## Site structure
- /categories returns 404. Category pages are /categories/<slug> and are linked from the homepage and every category page.
- Homepage shows "685 builds · 156 guides · 21 use cases", the launch post (Diogo Almeida, "20-200x faster, 40-400x cheaper"),
  a sponsor strip, and the full gallery of 685 cards in one page (no pagination/load-more; all cards are in the SSR HTML).
- A build page shows: platform + author eyebrow, title, subtitle, the embedded source post (X/GitHub/etc.), a site summary,
  "Open the source", a Details `dl` (Author, Use case = the ONE primary category, Added, plus author-reported metrics
  like Cost per task, Latency, Accuracy, ECE), a tag list, "All figures come from the author", and "More like this".
- GitHub builds show Stars/Forks/Language/Last push "read from the GitHub API on Sep 22, 2026".
- /community (74 items): a separate feed of user submissions. **Jev itself reads each submitted link, judges it and sorts
  it into a use case**. Each page shows "Jev judged this in N ms for $X" (e.g. 2,511 ms, $0.00013) and has upvote buttons.
  "The best of these get promoted into the main directory". 8 have no use case ("—"). /submit is the intake form (not used).
- Other sections (not projects): /resources (152 guides), /guides, /x-posts, /github-repos, /videos, /sites, /skills, /tools,
  /jev-for-seo, /jev-for-ads, /jev-for-marketing, /jev-as-a-judge, /jev-pricing, /jev-statistics, /jev-vs-llm, /jev-mcp,
  /jev-with/claude-code, /free-tools/ai-slop-detector, /sponsors, /search, /about. Publisher: Jon Kraayenbrink (@kraayenjon).

## Categories (count on the category page; builds can be in several; primary "Use case" count in brackets)
agents-and-browsers 163 [74] | benchmarks-and-evals 130 [76] | open-source 120 [95] | sdks-and-integrations 119 [38] |
games-and-real-time 72 [72] | routing-and-models 49 [38] | social-and-feeds 49 [33] | tools-and-apps 49 [47] |
coding-and-review 38 [31] | search 35 [24] | agent-context 31 [25] | security-and-abuse 29 [19] | documents-and-ocr 28 [21] |
inbox-and-support 23 [12] | ads-and-marketing 22 [14] | trading-and-markets 20 [20] | robotics-and-devices 14 [14] |
sales-and-leads 13 [12] | ui 13 [7] | seo 10 [9] | ecommerce 7 [4]. Total memberships 1034, 685 unique.

## UI-only observations
- Category pages have a category chip bar (All + 21) and a "Show" platform filter with counts (for example SEO:
  All 10 / X posts 5 / GitHub 4 / Sites 1). Filtering is client-side over the SSR list.
- Cards show a metric callout (for example "Pages up to 500 · Price BYOK or $1"), a platform badge, and a vote count. X posts show the
  embedded post, with video thumbnails that play inline.
- "Sponsored / Your card here" slot every 16 cards ($100/wk). Sidebar sponsors: OpenJEV, GIF Decider, JevHub, evoke, Vestra,
  blink.review ("Advertise from $19/wk"), with a public /sponsors/rules page.
- Platforms among builds: X 367, GitHub 331, Live site 37, Skill 12, Article 4, LinkedIn 4, Threads 3, YouTube 1.
- Top tags: open source 195, classification 41, benchmark 36, laya 45 (both spellings), demo 31, browser agent 30, local 27, mcp 23.

## Surprises / gaps
- "Open source" (95 primary) is mostly open-source *alternatives/clones* of Jev (Laya 421M, OpenJev Verdict, NanoJev, kev,
  jevk5, laya-* runtimes), not apps built on Jev. Titled "Open source alternatives to Jev".
- Jev's typed question kinds show up on the site as Choice / Noul (yes-no) / Score.
- Many builds are just X posts with benchmark claims. Metrics are author-reported and unverified (the site says so).
- Not done: I did not click through every card in the UI, and I did not read or judge each item by hand for decision or question type.

## Notes for source-scout
- The /community/ section has 74 pages (you already list them in proj_urls.txt). Their dl keys differ: Found by, Source,
  Use case, Judged in, Cost, Added. The "Jev judged this in" line is Jev's own classification of the submission.
- /categories (index) is a 404. Use the 21 /categories/<slug> pages.
- Build primary category = dd after dt "Use case". The tag list = the `<ul>` immediately after the Details `</dl>` in `<aside>`.
- Full HTML for all 759 pages is cached at ../bs/m/ (builds_<slug>.html, community_<slug>.html). Reuse it instead of re-fetching.
