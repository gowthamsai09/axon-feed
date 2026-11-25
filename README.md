# axon-feed

Plane 1 of [Axon](https://github.com/gowthamsai09/Axon): collects sources (no LLM, no keys), dedupes, tags and scores
items, and publishes static JSON shards. Spec: sections 4.1 and 5.

**Status:** Phases 1 and 5 built and tested locally; the first real Actions run has not happened (`docs/go-live.md`).
Collectors (arXiv, Hugging Face, Hacker News, GitHub releases, lab blogs), redirect resolution, rolling store,
publishing to the `data` branch, and, since Phase 5, stale-source Issues and an SSRF-hardened fetcher.

```bash
python -m scripts.validate_config      # offline: taxonomy, sources, authority
python -m scripts.validate_sources     # network: probe every verify:true source (run locally)
python -m pipeline.run --out ../feed-out --window-hours 72
python -m scripts.inspect_feed ../feed-out
```
Config lives in `config/` (`taxonomy.yaml`, `sources.yaml`, `authority.yaml`). Sources whose feed is unknown are listed
under `needs_research`; never add guessed feed URLs.

**Stale sources:** a source that returns nothing for 3 days in a row (`stale_after_days` per source; `skip_weekends` for
arXiv) opens one Issue labelled `source-stale`; it closes when the source recovers. The step is `scripts/sync_issues.py`
(needs `issues: write`, never fails the run). Mocked tests only until the first push.

## License

Apache-2.0. See `LICENSE` and `NOTICE`.
