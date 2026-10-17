# axon-feed

Plane 1 of [Axon](https://github.com/gowthamsai09/Axon): collects sources (no LLM, no keys), dedupes,
tags and scores items, and publishes static JSON shards. Spec: sections 4.1 and 5.

**Phase 0 status:** scaffolding only. Models, config validators and `scripts/validate_sources.py`
exist; collectors and pipeline stages are stubs.

```bash
python -m scripts.validate_config      # offline: taxonomy, sources, authority
python -m scripts.validate_sources     # network: probe every verify:true source (run locally)
```
Config lives in `config/` (`taxonomy.yaml`, `sources.yaml`, `authority.yaml`). Sources whose feed
is unknown are listed under `needs_research`; never add guessed feed URLs.
