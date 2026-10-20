"""Maps a configured source to its collector."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from urllib.parse import urlsplit

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


def host_limits(sources: Iterable[SourceEntry]) -> dict[str, float]:
    """host -> strictest rate_limit_per_min among the sources that fetch from it (spec A.3).
    arXiv's three feeds share one host, GitHub's six release feeds another."""
    limits: dict[str, float] = {}
    for source in sources:
        for _label, url in source.expand_urls():
            host = urlsplit(url).netloc.lower()
            if host:
                limits[host] = min(
                    limits.get(host, source.rate_limit_per_min), source.rate_limit_per_min
                )
    return limits
