"""Write the published layout (spec 5.4) to a directory. Stateless per run except archive/."""

from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from models.common import TOPIC_SLUGS
from models.feed_item import FeedItem

SCHEMA_VERSION = 1
LATEST_MAX_ITEMS = 150
LATEST_HOURS = 48


def _dump(item: FeedItem, *, with_excerpt: bool) -> dict[str, Any]:
    return item.model_dump(
        mode="json", exclude_none=True, exclude=None if with_excerpt else {"excerpt"}
    )


def _write(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(tmp, path)


def _prune(directory: Path, keep: set[str]) -> None:
    if directory.is_dir():
        for f in directory.glob("*.json"):
            if f.name not in keep:
                f.unlink()


def _shard(now: datetime, items: list[FeedItem], hours: float) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "window_hours": hours,
        "items": [_dump(i, with_excerpt=False) for i in items],
    }


def write_feed(
    out: Path,
    items: list[FeedItem],
    entity_slugs: dict[str, list[str]],
    *,
    now: datetime,
    window_hours: float,
    sources: list[dict[str, Any]],
    warnings: list[str],
) -> dict[str, int]:
    """items must already be sorted by base_score (descending). Returns file counts."""
    out.mkdir(parents=True, exist_ok=True)
    cutoff = now - timedelta(hours=min(window_hours, LATEST_HOURS))
    latest = [i for i in items if i.published_at >= cutoff][:LATEST_MAX_ITEMS]
    _write(out / "latest.json", _shard(now, latest, window_hours))

    by_tag: dict[str, list[FeedItem]] = {t: [] for t in TOPIC_SLUGS}
    by_entity: dict[str, list[FeedItem]] = defaultdict(list)
    for item in items:
        for t in item.tags:
            by_tag[t].append(item)
        for slug in entity_slugs.get(item.id, []):
            by_entity[slug].append(item)
    for tag, members in by_tag.items():
        _write(out / "tags" / f"{tag}.json", _shard(now, members, window_hours))
    for slug, members in by_entity.items():
        _write(out / "entities" / f"{slug}.json", _shard(now, members, window_hours))
    for item in items:
        _write(out / "items" / f"{item.id}.json", _dump(item, with_excerpt=True))
    _prune(out / "tags", {f"{t}.json" for t in by_tag})
    _prune(out / "entities", {f"{s}.json" for s in by_entity})
    _prune(out / "items", {f"{i.id}.json" for i in items})

    for item in items:  # archive: append-only, deduplicated by id
        d = item.published_at
        path = out / "archive" / f"{d:%Y}" / f"{d:%m}" / f"{d:%d}.jsonl"
        known: set[str] = set()
        if path.exists():
            known = {
                json.loads(ln)["id"] for ln in path.read_text(encoding="utf-8").splitlines() if ln
            }
        if item.id not in known:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(
                    json.dumps(
                        _dump(item, with_excerpt=True), ensure_ascii=False, separators=(",", ":")
                    )
                    + "\n"
                )

    counts = {
        "items": len(items),
        "latest": len(latest),
        "tags": len(by_tag),
        "entities": len(by_entity),
    }
    _write(
        out / "meta.json",
        {
            "schema_version": SCHEMA_VERSION,
            "generated_at": now.isoformat().replace("+00:00", "Z"),
            "window_hours": window_hours,
            "counts": counts,
            "sources": sources,
            "warnings": warnings[:200],
        },
    )
    return counts
