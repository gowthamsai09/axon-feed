"""Hacker News via the Algolia search API. Verified against a real capture (2026-10-16): the
query 'LLM' is typo-tolerant and returns non-AI stories ('limit', 'limited' matched), so hits
are gated on a whole-token LLM/LLMs in title or story text. Highlight fields are ignored.
Ask HN posts have no url: the item URL is the HN discussion page. story_text is HTML.
Unverified (Appendix B #13): the numericFilters/hitsPerPage/page parameters; the response echoes
its `params`, which is checked at runtime and reported as a warning if a parameter was ignored."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

from collectors.base import TEXT_MAX, Collected, RawItem
from collectors.feedparse import parse_date
from collectors.http import Fetcher, FetchError
from collectors.text import clean_text, truncate
from models.feed_item import Signals
from models.sources import SourceEntry

HN_ITEM = "https://news.ycombinator.com/item?id={id}"
GATE = re.compile(r"(?i)(?<![A-Za-z0-9])LLMs?(?![A-Za-z0-9])")
PAGE_SIZE = 100
MAX_PAGES = 3


def parse_hits(source: SourceEntry, body: bytes) -> tuple[Collected, int, str]:
    """(collected, number of hits on the page, echoed params)."""
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    hits = data.get("hits") if isinstance(data, dict) else None
    if not isinstance(hits, list):
        raise ValueError("no hits list")
    out = Collected()
    for h in hits:
        out.fetched += 1
        if not isinstance(h, dict):
            continue
        oid, title = str(h.get("objectID", "")), " ".join(str(h.get("title") or "").split())
        story = clean_text(str(h.get("story_text") or ""), max_chars=TEXT_MAX)
        if not oid.isdigit() or not title or not (GATE.search(title) or GATE.search(story)):
            continue
        try:
            out.items.append(
                RawItem(
                    source_id=source.id,
                    source_type=source.type,
                    url=str(h.get("url") or HN_ITEM.format(id=oid)),
                    title=truncate(title, 500),
                    published_at=parse_date(h.get("created_at")),
                    author=str(h["author"])[:200] if h.get("author") else None,
                    text=story,
                    signals=Signals(
                        points=max(int(h.get("points") or 0), 0),
                        comments=max(int(h.get("num_comments") or 0), 0),
                    ),
                )
            )
        except (ValueError, TypeError):
            out.warnings.append(f"{source.id}: skipped invalid hit {oid}")
    return out, len(hits), str(data.get("params", ""))


class HackerNewsCollector:
    def __init__(
        self, source: SourceEntry, *, window_hours: float = 24, now: datetime | None = None
    ) -> None:
        self.source, self.window_hours, self._now = source, window_hours, now

    def fetch(self, fetcher: Fetcher) -> Collected:
        now = self._now or datetime.now(UTC)
        since = int((now - timedelta(hours=self.window_hours)).timestamp())
        total = Collected()
        for page in range(MAX_PAGES):
            params: dict[str, str | int] = {
                "hitsPerPage": PAGE_SIZE,
                "page": page,
                "numericFilters": f"created_at_i>{since}",
            }
            try:
                body = fetcher.get(
                    self.source.url or "", per_min=self.source.rate_limit_per_min, params=params
                )
                part, n, echoed = parse_hits(self.source, body)
            except (FetchError, ValueError) as exc:
                total.warnings.append(f"{self.source.id}: {exc}")
                break
            total.items += part.items
            total.warnings += part.warnings
            total.fetched += part.fetched
            if page == 0 and ("numericFilters" not in echoed or "hitsPerPage=100" not in echoed):
                total.warnings.append(f"{self.source.id}: API ignored query parameters: {echoed}")
            if n < PAGE_SIZE:
                break
        total.failed = total.fetched == 0 and bool(total.warnings)
        return total
