"""Shared-feed pipeline entry point (spec 5.2, 5.5).

  python -m pipeline.run --out ../feed-out                    # development: last 24 hours
  python -m pipeline.run --out data/feed --window-hours 168   # production default (spec 5.6)

A failing source is logged and skipped; the run itself only fails on invalid config (exit 2).
State across runs: archive/ (append-only) and store.json (spec 5.6): the raw items still inside the
window plus the redirect cache. Each run merges what the sources return now with the stored items,
rebuilds every shard from the merged set, then saves the pruned store. `--no-store` runs stateless.
A shorter --window-hours than the previous run prunes the store to that window."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from collectors.base import Collected, RawItem
from collectors.http import PLACEHOLDER_CONTACT, Fetcher, Throttle, make_client
from collectors.registry import collector_for, host_limits
from collectors.text import truncate
from models.feed_item import AlsoCoveredBy, FeedItem, Signals
from models.loaders import ConfigError, load_authority, load_sources, load_taxonomy
from models.sources import SourceEntry
from pipeline.canonicalize import item_id_for
from pipeline.dedupe import Candidate, cluster
from pipeline.extract import enrich
from pipeline.publish import write_feed
from pipeline.redirects import DEFAULT_MAX_RESOLVES, resolve_redirects
from pipeline.score import base_score
from pipeline.store import (
    MAX_EXTRACT_ATTEMPTS,
    Key,
    Store,
    build_store,
    load_store,
    merge_with_store,
    save_store,
)
from pipeline.tag import Tagger
from pipeline.window import DEV_WINDOW_HOURS, effective_date, in_window

CONFIG = Path(__file__).resolve().parent.parent / "config"
SNIPPET_CHARS, EXCERPT_CHARS = 280, 2000


@dataclass
class Processed:
    items: list[FeedItem] = field(default_factory=list)
    entity_slugs: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def process(
    raws: list[RawItem],
    sources: dict[str, SourceEntry],
    tagger: Tagger,
    *,
    now: datetime,
    window_hours: float,
    redirects: Mapping[str, str] | None = None,
) -> Processed:
    """Window -> canonicalize -> dedupe/cluster -> tag -> score. Pure (no I/O).
    `redirects` maps an original URL to its resolved URL; ids and canonical URLs come from the
    resolved one while `url` stays as the source gave it."""
    res = Processed()
    cands: list[Candidate] = []
    raw_by_key: dict[tuple[str, str], RawItem] = {}
    for raw in raws:
        if not in_window(raw.published_at, now=now, hours=window_hours):
            continue
        try:
            iid, canon = item_id_for((redirects or {}).get(raw.url, raw.url))
        except ValueError as exc:
            res.warnings.append(f"{raw.source_id}: bad URL {raw.url!r}: {exc}")
            continue
        when, undated = effective_date(raw.published_at, now)
        if undated:
            res.warnings.append(f"{raw.source_id}: no date for {canon}; used fetch time")
        raw_by_key[(iid, raw.source_id)] = raw
        cands.append(
            Candidate(
                iid,
                canon,
                raw.title,
                raw.source_id,
                sources[raw.source_id].authority,
                when,
                raw.text,
            )
        )
    clusters = cluster(cands)
    sig = {
        c.primary.id: Signals(
            stars=max(raw_by_key[(m.id, m.source_id)].signals.stars for m in c.members),
            points=max(raw_by_key[(m.id, m.source_id)].signals.points for m in c.members),
            comments=max(raw_by_key[(m.id, m.source_id)].signals.comments for m in c.members),
        )
        for c in clusters
    }
    totals = {i: s.stars + s.points + s.comments for i, s in sig.items()}
    max_eng = max(totals.values(), default=0)
    max_size = max((c.size for c in clusters), default=1)
    for c in clusters:
        p = c.primary
        raw = raw_by_key[(p.id, p.source_id)]
        text = max((raw_by_key[(m.id, m.source_id)].text for m in c.members), key=len)
        defaults = list(
            dict.fromkeys(t for m in c.members for t in sources[m.source_id].default_tags)
        )
        tr = tagger.tag(
            raw.title, text, defaults, entities_title_only=raw.source_type == "github_releases"
        )
        age = (now - p.published_at).total_seconds() / 3600
        try:
            item = FeedItem(
                schema_version=1,
                id=p.id,
                url=raw.url,
                canonical_url=p.canonical_url,
                title=raw.title,
                source_id=p.source_id,
                source_type=raw.source_type,
                published_at=p.published_at,
                fetched_at=now,
                author=raw.author,
                snippet=truncate(text, SNIPPET_CHARS),
                excerpt=truncate(text, EXCERPT_CHARS) or None,
                tags=tr.tags,
                entities=tr.entities,
                signals=sig[p.id],
                cluster_id=f"c-{p.id[:8]}" if c.size > 1 else None,
                also_covered_by=[AlsoCoveredBy(source_id=s, url=u) for s, u in c.also_covered_by()],
                base_score=base_score(
                    age_hours=age,
                    authority=p.authority,
                    engagement_total=totals[p.id],
                    max_engagement=max_eng,
                    cluster_size=c.size,
                    max_cluster_size=max_size,
                ),
            )
        except ValueError as exc:
            res.warnings.append(f"{p.source_id}: invalid item {p.canonical_url}: {str(exc)[:160]}")
            continue
        res.items.append(item)
        res.entity_slugs[item.id] = tr.entity_slugs
    res.items.sort(key=lambda i: (-i.base_score, i.id))
    return res


def collect_all(
    sources: list[SourceEntry],
    fetcher: Fetcher,
    *,
    window_hours: float,
    now: datetime | None = None,
) -> dict[str, Collected]:
    results: dict[str, Collected] = {}
    for s in sources:
        try:
            results[s.id] = collector_for(s, window_hours=window_hours, now=now).fetch(fetcher)
        except Exception as exc:  # a collector bug must not fail the run
            results[s.id] = Collected(
                warnings=[f"{s.id}: collector crashed: {type(exc).__name__}"], failed=True
            )
    return results


def source_health(
    collected: dict[str, Collected], kept: dict[str, int], retained: dict[str, int] | None = None
) -> list[dict[str, Any]]:
    """`in_window` counts everything in the window, including `retained` items that only the
    store still holds (the source no longer returns them)."""
    rows = []
    for sid, c in collected.items():
        status = "failed" if c.failed else ("empty" if c.fetched == 0 else "ok")
        rows.append(
            {"id": sid, "status": status, "fetched": c.fetched, "collected": len(c.items),
             "in_window": kept.get(sid, 0), "retained": (retained or {}).get(sid, 0),
             "warnings": c.warnings[:10]}
        )  # fmt: skip
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--window-hours", type=float, default=DEV_WINDOW_HOURS)
    ap.add_argument("--config-dir", type=Path, default=CONFIG)
    ap.add_argument("--only", help="run a single source id")
    ap.add_argument("--max-extract", type=int, default=30)
    ap.add_argument("--max-redirects", type=int, default=DEFAULT_MAX_RESOLVES)
    ap.add_argument("--store", type=Path, help="rolling store file (default: <out>/store.json)")
    ap.add_argument("--no-store", action="store_true", help="stateless run: no store read or write")
    ap.add_argument("--contact", default=PLACEHOLDER_CONTACT)
    ap.add_argument("--now", help="ISO time to use as now (replay captured data)")
    args = ap.parse_args(argv)
    try:
        taxonomy = load_taxonomy(args.config_dir / "taxonomy.yaml")
        cfg = load_sources(args.config_dir / "sources.yaml")
        load_authority(args.config_dir / "authority.yaml")
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    if args.out.exists() and any(args.out.iterdir()) and not (args.out / "meta.json").exists():
        print(
            f"Refusing to write into {args.out}: it is not empty and is not a previous feed "
            "output (no meta.json). Use a new or empty folder, e.g. ..\\feed-out.",
            file=sys.stderr,
        )
        return 2
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    sources = [s for s in cfg.sources if args.only in (None, s.id)]
    by_id = {s.id: s for s in cfg.sources}
    store_path = None if args.no_store else (args.store or args.out / "store.json")
    store, store_warnings = load_store(store_path, now=now) if store_path else (None, [])
    previous = store or Store(saved_at=now)
    per_min = {s.id: s.rate_limit_per_min for s in cfg.sources}
    attempted: set[Key] = set()
    with make_client(args.contact) as client:
        fetcher = Fetcher(client, Throttle(host_limits=host_limits(cfg.sources)))
        collected = collect_all(sources, fetcher, window_hours=args.window_hours, now=now)
        raws = [r for c in collected.values() for r in c.items]
        fresh = [r for r in raws if in_window(r.published_at, now=now, hours=args.window_hours)]
        merged = merge_with_store(previous.items, fresh, now=now, window_hours=args.window_hours)
        windowed = merged.raws
        resolved, rwarn = resolve_redirects(
            windowed, fetcher, per_min, max_fetches=args.max_redirects, known=previous.redirects
        )
        redirects = {**previous.redirects, **resolved}
        tried_enough = {k for k, n in merged.extract_tried.items() if n >= MAX_EXTRACT_ATTEMPTS}
        windowed, xwarn = enrich(
            windowed,
            fetcher,
            per_min,
            max_fetches=args.max_extract,
            skip=tried_enough,
            attempted=attempted,
        )
    res = process(
        windowed,
        by_id,
        Tagger(taxonomy),
        now=now,
        window_hours=args.window_hours,
        redirects=redirects,
    )
    kept: dict[str, int] = {}
    for r in windowed:
        kept[r.source_id] = kept.get(r.source_id, 0) + 1
    new_store, build_warnings = build_store(
        windowed, merged, now=now, redirects=redirects, attempted=attempted
    )
    warnings = (
        [w for c in collected.values() for w in c.warnings]
        + store_warnings
        + rwarn
        + xwarn
        + res.warnings
        + (build_warnings if store_path else [])
    )
    health = source_health(collected, kept, merged.retained)
    counts = write_feed(
        args.out, res.items, res.entity_slugs, now=now, window_hours=args.window_hours,
        sources=health, warnings=warnings,
    )  # fmt: skip
    if store_path:
        save_store(store_path, new_store)
    print(f"window {args.window_hours:g}h: {counts}")
    if store_path:
        n_kept = sum(merged.retained.values())
        print(
            f"store: {len(new_store.items)} items "
            f"({len(new_store.items) - n_kept} collected now, {n_kept} retained)"
        )
    for row in health:
        fetched, kept_n = row["fetched"], row["in_window"]
        print(
            f"  {row['status']:<7} {row['id']:<22} fetched={fetched} in_window={kept_n}"
            f" retained={row['retained']}"
        )
        if row["status"] != "ok":
            for w in row["warnings"][:2]:
                print(f"      ! {w}")
    print(f"{len(warnings)} warning(s); wrote {args.out}")
    if collected and all(r["status"] != "ok" for r in health):
        print(
            "ALL SOURCES FAILED: nothing was collected (see the '!' lines above).", file=sys.stderr
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
