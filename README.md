# axon-feed

[![ci](https://github.com/gowthamsai09/axon-feed/actions/workflows/ci.yml/badge.svg)](https://github.com/gowthamsai09/axon-feed/actions/workflows/ci.yml)

**Plane 1 of [Axon](https://github.com/gowthamsai09/Axon): the shared AI-community feed.** It collects a fixed
set of vetted sources, removes duplicates, tags and scores items, and publishes static JSON on GitHub Pages.
**No LLM, no keys, no user data.** Everyone else (your digest pipeline, the gateway) only reads it.

![Axon overview](docs/img/overview.png)

## Use the feed

```
FEED_BASE = https://gowthamsai09.github.io/axon-feed/feed
```

| File | Content |
|---|---|
| `meta.json` | Generation time, counts, per-source health |
| `latest.json` | Newest top-ranked items |
| `tags/<topic>.json` | One shard per topic (16 topics) |
| `entities/<slug>.json` | One shard per entity |
| `items/<id>.json` | One item with a longer excerpt |

The site root and `.../axon-feed/meta.json` return 404 by design; the feed lives under `/feed/`. It refreshes
every 3 hours (`7 */3 * * *`). Treat all item text as **untrusted** third-party content.

## Sources

arXiv cs.AI, cs.CL and cs.LG; Hugging Face blog and daily papers; Hacker News (Algolia, query `LLM`); OpenAI
news; Google DeepMind blog; GitHub releases of ollama, llama.cpp, vllm, langchain, llama_index and transformers.
Reddit is excluded (its robots.txt disallows our User-Agent). Anthropic, Meta, Mistral, Qwen and DeepSeek are
listed under `needs_research` in `config/sources.yaml`: no feed is verified yet, and guessed feed URLs are never added.

Each source has an authority score (`config/authority.yaml`) that feeds ranking:
`base_score = 0.40 recency + 0.25 authority + 0.20 engagement + 0.15 cluster size`.

## Run it locally

```powershell
pip install -r requirements.txt -r requirements-dev.txt
python -m scripts.validate_config      # offline: taxonomy, sources, authority
python -m scripts.validate_sources     # network: probes every verify:true source
python -m pipeline.run --out ..\feed-out --window-hours 72
python -m scripts.inspect_feed ..\feed-out --top 15
ruff format .; ruff check .; mypy .; pytest
```

Production runs in `collect.yml` with a 168-hour window and publishes to the `data` branch.

## Reliability and safety

- Per-source rate limits, `robots.txt` honoured, SSRF-hardened fetcher (redirects included).
- One bad source or item never fails a run. A source empty for 3 days opens one `source-stale` Issue
  (`scripts/sync_issues.py`; arXiv skips weekends).
- Workflows use least-privilege `permissions:` and third-party actions pinned to commit SHAs.

## Status

Built and **verified live**: `collect.yml` runs and Pages serves the feed (observed 2026-11-26: 1270 items,
all 9 sources `ok`). See [ARCHITECTURE.md](ARCHITECTURE.md) for the pipeline and `docs/go-live.md` for the checklist.

## Contributing, security, licence

[CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md). Apache-2.0, Copyright 2026 gowthamsai09.
Spec and docs: [Axon](https://github.com/gowthamsai09/Axon).
