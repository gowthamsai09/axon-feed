# axon-feed architecture

`axon-feed` is **plane 1** of [Axon](https://github.com/gowthamsai09/Axon): the shared feed. It collects
a fixed set of vetted AI-community sources, removes duplicates, tags and scores the items, and publishes
static JSON. It uses **no LLM and no keys**, holds **no user data**, and runs on GitHub Actions and Pages.

Spec sections: 4.1 and 5. Where this file and the spec differ, the spec wins.

![Axon overview](docs/img/overview.png)

The feed is the left-hand plane of the diagram. Plane 2 (user digests) and plane 3 (the A2A and MCP
gateway) only ever **read** it, over HTTPS, from GitHub Pages.

## What it produces

Published to the `data` branch and served by Pages **under `/feed/`**:

| File | Content |
|---|---|
| `meta.json` | Generation time, item counts, per-source health, warnings |
| `latest.json` | The newest, highest-ranked items (150 in the maintainer's run) |
| `tags/<topic>.json` | One shard per topic (16 topics) |
| `entities/<slug>.json` | One shard per entity (about 29) |
| `items/<id>.json` | One file per item, with a longer excerpt used by summarizers |
| `archive/` | Older items kept by the rolling store |
| `store.json` | The rolling store (state for dedupe and source health) |

Consumers:

```
FEED_BASE = https://gowthamsai09.github.io/axon-feed/feed
meta.json = https://gowthamsai09.github.io/axon-feed/feed/meta.json
```

The site root and `.../axon-feed/meta.json` return 404 on purpose.

Item ids are 16 hex characters. Shard and entity names are slugs, which is why the gateway can turn a
request into a file name without ever building a path from free text.

Observed on 2026-11-26: 1270 items, 150 in `latest`, 16 tags, 20 entities with items, all 9 sources `ok`,
none stale. Only expected extract warnings (403 and 429 from other sites, the 30-page-fetch cap).

## Pipeline

```
 cron 7 */3 * * *  (collect.yml, contents: write, issues: write)
        |
        v
  load store ──> collect ──> window ──> merge with store ──> resolve redirects
                    |                                              |
          (9 source configs)                                       v
                                                              enrich (extract text)
                                                                   |
                                                                   v
                              canonicalize ──> dedupe ──> tag ──> score
                                                                   |
                                                                   v
                                       publish shards ──> save store ──> `data` branch ──> Pages
                                                                   |
                                                                   v
                                                   sync_issues (stale-source Issues)
```

Order in `pipeline/run.py`: load store, collect, window, merge with store, resolve redirects, enrich,
process (canonicalize, dedupe, tag, score), publish, save store.

| Module | Job |
|---|---|
| `collectors/` | `arxiv`, `rss`, `github_releases`, `hf`, `hn`; a shared `http` layer with `Throttle`, `Robots` and `Fetcher` |
| `pipeline/window.py` | Keep items in the window: 24 h in development, **168 h in production** |
| `pipeline/redirects.py` | Resolve redirects (SSRF-guarded) so the canonical URL is the real one |
| `pipeline/extract.py` | Pull readable text for the excerpt; capped at 30 page fetches per run |
| `pipeline/canonicalize.py` | Strip tracking parameters and normalise URLs |
| `pipeline/dedupe.py` | Merge the same story from several places into one item with a cluster size |
| `pipeline/tag.py` | Assign topics and entities from `config/taxonomy.yaml` |
| `pipeline/score.py` | Compute `base_score` |
| `pipeline/publish.py` | Write the shards |
| `pipeline/store.py`, `health.py` | Rolling store and per-source staleness |
| `scripts/` | `validate_config`, `validate_sources`, `inspect_feed`, `sync_issues`, `publish_data`, `capture_samples`, `export_schemas` |

## Scoring

```
base_score = 0.40 * recency + 0.25 * authority + 0.20 * engagement + 0.15 * cluster_size_norm
```

`authority` comes from the source's score in `config/sources.yaml`, bounded by the tier table in
`config/authority.yaml`. It says how much to trust a source's own claims, not how interesting an item is.

| Tier | Range | Used by |
|---|---|---|
| primary | 0.85 to 1.00 | OpenAI news (0.90), Google DeepMind blog (0.90) |
| platform | 0.70 to 0.84 | Hugging Face blog (0.80), HF daily papers (0.75), GitHub releases (0.70) |
| curated-aggregate | 0.55 to 0.69 | arXiv cs.AI (0.60), cs.CL (0.60), cs.LG (0.55), Hacker News (0.55) |
| community | 0.35 to 0.54 | none today |
| unvetted | 0.00 to 0.34 | none; not used by default |

A new source starts at 0.50 and may not exceed 0.60 until 30 days of health data exist in `meta.json`.

## Sources: why these

| Source | Why it is in |
|---|---|
| arXiv cs.AI, cs.CL, cs.LG | The primary channel for research papers; high volume, so a lower authority prior. Empty on Saturday and Sunday, so staleness skips weekends |
| Hugging Face blog | First-party news from the main open-model hub |
| Hugging Face daily papers | Community-voted papers with an engagement signal |
| Hacker News (Algolia, query `LLM`) | Early signal and engagement for tools and releases; claims are second-hand, hence the lower authority |
| OpenAI news | First-party announcements |
| Google DeepMind blog | First-party announcements and research |
| GitHub releases (ollama, llama.cpp, vllm, langchain, llama_index, transformers) | The release notes of the tools most people run locally |

Each of these has a **verified** feed or API, probed with `scripts/validate_sources.py`.

**Not included, and why**
- **Reddit (r/LocalLLaMA, r/MachineLearning):** `robots.txt` disallowed our User-Agent (probe 2026-10-16).
  Axon does not scrape what a site forbids. A future path would be the official API under Reddit's terms.
- **Anthropic, Meta AI, Mistral, Qwen, DeepSeek blogs:** listed under `needs_research` in `sources.yaml`.
  No feed is verified yet. The rule is: never add a guessed feed URL. Qwen and DeepSeek may be better
  covered through GitHub releases or Hugging Face organisations than a blog.

The set is deliberately small: every source carries a maintenance cost and a trust judgement.

## Politeness and safety

- Per-source rate limits (`rate_limit_per_min`), `robots.txt` honoured, and a descriptive User-Agent.
- **SSRF-hardened fetcher:** every outbound URL, including redirects, passes the guard, so a hostile feed
  entry cannot point the collector at a private address.
- **Stale-source Issues:** a source returning nothing for 3 days in a row (`stale_after_days`, weekends
  skipped for arXiv) opens one Issue labelled `source-stale`; it closes when the source recovers. The step never fails the run.
- **Fail soft:** one bad source or item never fails the run; it is recorded in `meta.json` warnings.
- **Untrusted content:** feed text is data. The feed never interprets it, and consumers are told so
  (`untrusted: true`).
- **No keys, no LLM:** nothing to leak, and no per-run cost.

## Workflows

| Workflow | Trigger | Permissions | Notes |
|---|---|---|---|
| `collect.yml` | cron `7 */3 * * *`, manual | `contents: write`, `issues: write` | Third-party actions pinned to commit SHAs; publishes to the `data` branch |
| `ci.yml` | push, PR | read | Ruff, mypy, pytest |

## Run it locally

```powershell
python -m scripts.validate_config                       # offline: taxonomy, sources, authority
python -m scripts.validate_sources                      # network: probes every verify:true source
python -m pipeline.run --out ..\feed-out --window-hours 72
python -m scripts.inspect_feed ..\feed-out --top 15
ruff format .; ruff check .; mypy .; pytest
```

On the maintainer's company laptop the live probes need a network that resolves the source hosts.
Collection ran in GitHub Actions.

## Design decisions worth knowing

- **Static JSON on Pages, not an API.** Free, cacheable at the edge and trivially mirrored; consumers
  filter by reading small shards.
- **Shards by tag and entity.** The gateway can answer a topic query by reading one or two small files.
- **Window of 168 hours in production.** Long enough to carry weekly digests and trend clusters.
- **No LLM in the feed.** Keeps the shared plane free, deterministic and impossible to prompt-inject
  into leaking anything; summarisation happens only with someone's own key.

## Not verified yet

- Exact test count for this repo (reported green; about 446, derived).
- Long-run behaviour of source health over weeks (needs more feed runs).
