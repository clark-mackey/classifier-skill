# source-scout: madewithjev.com (curl only, fetched 2026-09-24)

## Output
- `source-projects.jsonl`: 759 records (685 `/builds/*` + 74 `/community/*`), `source:"source"`.
- Raw HTML cache: `raw/pages/` (759 files, 173 MB), listing pages in `raw/cats/`, `raw/sitemap.xml`, `raw/llms.txt`, `raw/stats.json`.
- Parser: `raw/parse.py` (stdlib regex over SSR HTML).

## Sitemap and robots
- robots.txt: `Allow: /`, `Disallow: /*_rsc=`, one sitemap. No sitemap index or nested sitemaps.
- sitemap.xml: 974 URLs. 685 `/builds/<slug>`, 153 `/resources/<slug>`, 75 `/community` (index + 74), 21 `/categories/<slug>`, 8 `/free-tools/*`, 2 `/sponsors*`, plus about 30 single guide pages (`/what-is-jev`, `/jev-pricing`, `/jev-for-seo`, `/jev-with/claude-code`, `/github-repos`, `/skills`, `/sites`, `/x-posts`, `/videos`, `/submit`, and others).
- `/categories` (no slug) returns 404. Use `/categories/<slug>`.
- Project lastmod: 09-15 (2), 09-16 (5), 09-17 (8), 09-18 (121), 09-19 (52), 09-20 (50), 09-21 (77), 09-22 (207), 09-23 (172), 09-24 (63), 09-25 (2, community).
- Cross-check: every /builds and /community link on the homepage, the 21 category pages, /community, /github-repos, /skills, /sites, /tools, /x-posts and /videos is in the sitemap, and every sitemap project URL is linked from one of them (0 gaps either way).

## Data sources found in source
1. **Per-page SSR HTML** (Next.js App Router). There is no `__NEXT_DATA__`. The `self.__next_f.push` RSC payload only holds the rendered React tree, with no separate data objects and no fields hidden from the UI.
2. **JSON-LD** on every page: sitewide `@graph` (WebSite, Person publisher "Jon Kraayenbrink" with X/Threads/LinkedIn/GitHub sameAs, SoftwareApplication "Jev" -> typesafe.ai), plus per-page `Article` (headline, description, url, datePublished, author.name, and image on community pages) and `BreadcrumbList`.
3. **Meta**: title "<name> — Made with Jev", description equals the subtitle, og:type=article, og:image `/builds/<slug>/opengraph-image?<hash>` (community pages use the source's image, such as opengraph.githubassets.com or LinkedIn), and a fixed sitewide keywords list.
4. **`/llms.txt`**: guide and category index, with one description line per category.
5. **`/jev-statistics.json`** (open data, free to cite with a link). It is not in the sitemap, and nothing links to it except llms.txt and /jev-statistics. It holds build totals, byDay, bySource, byCategory, author counts, cost rows (n=15, median $0.000068/decision), latency rows (n=19, median 300 ms), throughput rows (n=13), and a GitHub snapshot for 276 repos taken 2026-09-22 (stars, forks, created, pushed, language). I joined these onto records as `published_cost`, `published_latency`, `published_throughput` and `github_snapshot`.

## Page schema (what the parser pulls)
- Breadcrumb -> primary `site_category`. The eyebrow reads "<Platform> · by <Author>" (builds) or "· found by <handle>" (community).
- h1 `name`, subtitle `description`, and the embedded source post (`embed_text`: the full X/LinkedIn post text, `embed_meta`: date and likes). There are 395 embeds, and they often contain the pipeline details the summary shortens.
- Site-written `summary` paragraph plus an "Open the source" link. GitHub summaries end with "Repository created ... Counts read from the GitHub API on Sep 22".
- **Details `<dl>`**: the free-form key/value list has 148 distinct keys. The common ones are Author (685), Use case, Added, Stars (177), Cost (106), Time, Run time, Speed, Latency, Parameters and Volume. There is a long tail of metric keys ("Questions per call", "Choice ceiling", "Held-out set", "Cost per decision", and others). All are kept raw in `details`.
- Tag `<ul>`: 650 distinct tags. Top ones: open source 195, classification 41, benchmark 36, laya 34 (plus "Laya" 11, a casing duplicate), demo, browser agent, local, mcp, claude code, model routing, calibration.
- Community pages add a chip reading "Jev judged this in N ms for $X" and the Details fields Found by / Source / Judged in / Cost. The site runs its own Jev call on each submission, and all 74 pages carry these figures.
- No `<pre>` or `<code>` on any project page. **No state/questions JSON, criteria definitions or code snippets are embedded anywhere.** Question types appear only in prose.

## Hidden or non-obvious fields
- `/submit` form: `url` (max 500), `handle` (max 40), and a honeypot input `company` (tabindex=-1, visually hidden) that trips bots. It posts to a Next.js server action; nothing was submitted.
- `jev-statistics.json` per-decision and per-second arithmetic (site-computed from author figures) and GitHub fork, created and pushed dates. Most of this is not shown on build pages.
- JSON-LD `datePublished` has a full timestamp on community pages (for example 2026-09-25T03:01:08Z). Build pages give only a date.
- 292 builds appear on more than one category page, recorded in `all_category_slugs`. The breadcrumb shows only the primary category.
- 8 community items have Use case "—" (no category): mostly launch posts by kraayenjon, plus Jevals, OpenJev code camp, refix and enhance-cx.

## Categories (21; record counts include community)
Open source 96 · Tools and apps 90 · Agents and browsers 82 · Games and real time 81 · Benchmarks and evals 76 · Routing and model choice 38 · SDKs and integrations 38 · Social feeds 33 · Coding and code review 32 · Context and memory 25 · Search 24 · Trading and markets 21 · Documents and OCR 21 · Security and abuse 19 · Robotics and devices 15 · Ads and marketing 14 · Inbox and support 12 · Sales and leads 12 · UI 9 · SEO and GEO 9 · Ecommerce 4 · (none) 8.
Source platforms: X 367, GitHub 331, Live site 37, Skill 12, LinkedIn 4, Article 4, Threads 3, YouTube 1.

## Field notes (heuristic, needs a semantic pass)
- `question_types_explicit`: 25 records name choice/noul/score in text. These are mostly SDKs, MCP servers, skills and replicas (typesafe-mcp, mcp-jev, jev-rs, pg-typesafe, augustus, laya, kev-open-engine, is-malicious [noul], jev-as-judge [noul, score]).
- `question_types_inferred`: keyword-based (choice 215, score 147, noul 79). `decision` is the first judging sentence of the summary, or the subtitle when none is found. `options_criteria` covers 42 records, where the text states label or topic counts (for example 1kpapers: "24 possible topics"). `input_kind` is a keyword list. `domain` equals site_category.
- `chaining_notes` and `threshold_notes` are sentences pulled from the text (for example bouncer: questions and thresholds in a YAML file, with published calibration results). `integrations_mentioned` counts: MCP 42, Claude Code 38, Codex 31, Chrome extension 15, GPT 15, Vercel 15, Grok 13, OpenRouter 12, Browser Use 16.
- Published metrics: 15 cost rows, 19 latency rows and 13 throughput rows in the stats JSON. Details "Cost" appears on 106 pages as free text ("3.5 cents", "~$0.04/day").

## Gaps
- Criteria and options wording appears only when the author's post or README spells it out. Answering those fields needs the linked source (GitHub READMEs, 343 records), which I did not fetch because it is off-site.
- The community feed is live and growing (items dated 09-25 already). The sitemap will drift.

## Notes for browser-scout
- Both URL sets match exactly (759 = 759, 0 gaps either way), so there are no missing URLs to hand over. 292 multi-category builds agree with your count.
- Items worth merging from my side: `jev-statistics.json` joins (cost, latency, throughput, GitHub stars and forks for 258 records), the full `details` dl (148 keys, including community "Judged in"/"Cost"), `embed_meta` (likes and dates), JSON-LD timestamps, and the `/submit` honeypot `company`.
