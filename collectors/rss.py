"""Generic RSS/Atom collector (openai-news, google-deepmind-blog, hf-blog, ...)."""

from __future__ import annotations

from collectors.base import TEXT_MAX, Collected, RawItem
from collectors.feedparse import FeedEntry, parse_feed
from collectors.http import Fetcher, FetchError
from collectors.text import clean_text, truncate
from models.sources import SourceEntry


def entry_to_item(
    source: SourceEntry, e: FeedEntry, *, title: str | None = None, text: str | None = None
) -> RawItem | None:
    """None if the entry has no usable title or link."""
    title = (title or e.title).strip()
    if not title or not e.link:
        return None
    body = clean_text(e.body, max_chars=TEXT_MAX, is_html=e.body_is_html) if text is None else text
    try:
        return RawItem(
            source_id=source.id,
            source_type=source.type,
            url=e.link,
            title=truncate(title, 500),
            published_at=e.date,
            author=truncate(e.author, 200) if e.author else None,
            text=body,
        )
    except ValueError:
        return None


def parse_rss(source: SourceEntry, body: bytes) -> Collected:
    out = Collected()
    for e in parse_feed(body):
        out.fetched += 1
        item = entry_to_item(source, e)
        if item is None:
            out.warnings.append(f"{source.id}: skipped entry without title/link")
        else:
            out.items.append(item)
    return out


class RssCollector:
    def __init__(self, source: SourceEntry) -> None:
        self.source = source

    def fetch(self, fetcher: Fetcher) -> Collected:
        try:
            body = fetcher.get(self.source.url or "", per_min=self.source.rate_limit_per_min)
            res = parse_rss(self.source, body)
            if res.fetched == 0:
                res.warnings.append(f"{self.source.id}: response had 0 entries ({len(body)} bytes)")
            return res
        except (FetchError, ValueError) as exc:
            return Collected(warnings=[f"{self.source.id}: {exc}"], failed=True)
