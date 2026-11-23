"""Rolling raw-item store: what lets a stateless Actions run keep a 7-day window (spec 5.6).

Sources only return what they hold today (arXiv: the current announcement day, empty at weekends),
so each run merges what it collected with the previous run's raw items, re-runs dedupe, tagging
and scoring over the merged set, and saves the pruned result for the next run. Stored per item:
the raw item as it entered the pipeline (after redirect resolution and page extraction), the
first time it was seen, and how often a page fetch was tried. Also stored: the redirect cache.

This is operator state next to meta.json. It is separate from archive/, which stays append-only.
A missing, corrupt or wrong-version store is ignored with a warning (the run starts empty)."""

from __future__ import annotations

import os
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, Field

from collectors.base import RawItem
from models.common import StrictModel
from pipeline.window import in_window

MAX_STORE_ITEMS = 6000  # safety cap (assumption): newest items win
MAX_EXTRACT_ATTEMPTS = 2  # page fetches per item across runs; 403s are not retried forever

Key = tuple[str, str]  # (source_id, url as the source gave it)


def key_of(raw: RawItem) -> Key:
    return (raw.source_id, raw.url)


class StoredItem(RawItem):
    first_seen: AwareDatetime
    extract_tried: int = Field(0, ge=0, le=100)


class SourceHealth(StrictModel):
    """Per source: when it last returned items and since when it has returned none (Appendix C 83)."""

    last_items_at: AwareDatetime | None = None
    empty_since: AwareDatetime | None = None


class Store(StrictModel):
    version: Literal[1] = 1
    saved_at: AwareDatetime
    items: list[StoredItem] = Field(default_factory=list)
    redirects: dict[str, str] = Field(default_factory=dict)
    health: dict[str, SourceHealth] = Field(default_factory=dict)  # absent in older stores


def load_store(path: Path, *, now: datetime) -> tuple[Store, list[str]]:
    """(store, warnings). Never raises: an unusable file means an empty store."""
    if not path.exists():
        return Store(saved_at=now), []
    try:
        return Store.model_validate_json(path.read_text(encoding="utf-8")), []
    except (OSError, ValueError) as exc:  # pydantic's ValidationError is a ValueError
        reason = f"{type(exc).__name__}: {str(exc)[:100]}"
        return Store(saved_at=now), [f"store: ignored unreadable {path.name} ({reason})"]


def save_store(path: Path, store: Store) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(store.model_dump_json() + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


@dataclass
class Merged:
    raws: list[RawItem]  # this run's items first (in order), then retained ones
    first_seen: dict[Key, datetime] = field(default_factory=dict)
    extract_tried: dict[Key, int] = field(default_factory=dict)
    retained: dict[str, int] = field(default_factory=dict)  # per source: kept only by the store


def merge_with_store(
    stored: list[StoredItem], new: list[RawItem], *, now: datetime, window_hours: float
) -> Merged:
    """New items win (fresh signals and title) except where the stored copy knows more: a longer
    (extracted) text and a publication date the source stopped giving. Stored items the sources
    no longer return are retained while they are inside the window."""
    old = {key_of(s): s for s in stored}
    merged = Merged(raws=[])
    seen: set[Key] = set()
    for raw in new:
        k = key_of(raw)
        s = old.get(k)
        update: dict[str, object] = {}
        if s is not None:
            if raw.published_at is None and s.published_at is not None:
                update["published_at"] = s.published_at
            if len(s.text) > len(raw.text):
                update["text"] = s.text
        merged.raws.append(raw.model_copy(update=update) if update else raw)
        merged.first_seen[k] = s.first_seen if s else now
        merged.extract_tried[k] = s.extract_tried if s else 0
        seen.add(k)

    def when(s: StoredItem) -> datetime:
        return s.published_at or s.first_seen

    keep = [
        s
        for k, s in old.items()
        if k not in seen and in_window(when(s), now=now, hours=window_hours)
    ]
    keep.sort(key=lambda s: (-when(s).timestamp(), s.source_id, s.url))
    for s in keep:
        k = key_of(s)
        merged.raws.append(
            RawItem.model_validate(s.model_dump(exclude={"first_seen", "extract_tried"}))
        )
        merged.first_seen[k] = s.first_seen
        merged.extract_tried[k] = s.extract_tried
        merged.retained[s.source_id] = merged.retained.get(s.source_id, 0) + 1
    return merged


def build_store(
    raws: list[RawItem],
    merged: Merged,
    *,
    now: datetime,
    redirects: Mapping[str, str],
    attempted: Collection[Key],
) -> tuple[Store, list[str]]:
    """The store to save after a run: the (enriched) raw items still in the window, undated items
    pinned to the time they were first seen, and the redirect cache pruned to those items."""
    items: list[StoredItem] = []
    for raw in raws:
        k = key_of(raw)
        first = merged.first_seen.get(k, now)
        tried = merged.extract_tried.get(k, 0) + (1 if k in attempted else 0)
        data = raw.model_dump()
        data["published_at"] = raw.published_at or first
        items.append(StoredItem(**data, first_seen=first, extract_tried=tried))
    warnings: list[str] = []
    if len(items) > MAX_STORE_ITEMS:
        items.sort(
            key=lambda s: (-(s.published_at or s.first_seen).timestamp(), s.source_id, s.url)
        )
        warnings.append(f"store: kept the newest {MAX_STORE_ITEMS} of {len(items)} items")
        items = items[:MAX_STORE_ITEMS]
    urls = {i.url for i in items}
    cache = {u: f for u, f in redirects.items() if u in urls}
    return Store(saved_at=now, items=items, redirects=cache), warnings
