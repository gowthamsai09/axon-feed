"""Maps a configured source to its collector."""

from __future__ import annotations

from datetime import datetime

from collectors.arxiv import ArxivCollector
from collectors.base import Collector
from collectors.github_releases import GithubReleasesCollector
from collectors.hf import HfDailyPapersCollector
from collectors.hn import HackerNewsCollector
from collectors.rss import RssCollector
from models.sources import SourceEntry

API_COLLECTORS = {"hf-daily-papers", "hn-ai"}


def collector_for(
    source: SourceEntry, *, window_hours: float, now: datetime | None = None
) -> Collector:
    if source.type == "github_releases":
        return GithubReleasesCollector(source)
    if source.type == "rss":
        return ArxivCollector(source) if source.id.startswith("arxiv-") else RssCollector(source)
    if source.id == "hf-daily-papers":
        return HfDailyPapersCollector(source)
    if source.id == "hn-ai":
        return HackerNewsCollector(source, window_hours=window_hours, now=now)
    raise ValueError(f"no collector for api source {source.id!r}")
