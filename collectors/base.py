"""Collector contract (spec 4.1, 5.1): fetch() -> Collected. Pure parse functions do the work."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from pydantic import AwareDatetime, Field

from collectors.http import Fetcher
from models.common import HttpUrlStr, SourceId, SourceType, StrictModel
from models.feed_item import Signals
from models.sources import SourceEntry

TEXT_MAX = 4000  # chars of cleaned feed text kept per item; the excerpt is cut from it (2000)


class RawItem(StrictModel):
    """What a collector returns before canonicalize/dedupe/tag/score."""

    source_id: SourceId
    source_type: SourceType
    url: HttpUrlStr
    title: str = Field(min_length=1, max_length=500)
    published_at: AwareDatetime | None = None  # None: pipeline substitutes fetch time + warning
    author: str | None = Field(None, max_length=200)
    text: str = Field("", max_length=50_000)  # cleaned plain text from the feed
    signals: Signals = Field(default_factory=Signals)


@dataclass
class Collected:
    items: list[RawItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fetched: int = 0  # entries seen before any skipping
    failed: bool = False  # True when the request or parse itself failed (not merely empty)


class Collector(Protocol):
    source: SourceEntry

    def fetch(self, fetcher: Fetcher) -> Collected:
        """Must not raise for a failing URL or a bad entry: record a warning and carry on."""
        ...
