"""arXiv RSS (cs.AI / cs.CL / cs.LG). Verified against real captures (2026-10-16):
RSS 2.0, <link> is https://arxiv.org/abs/<id> (no version), pubDate is the announcement day at
00:00 -0400, <description> starts 'arXiv:<id>vN Announce Type: <type> Abstract: ...', and
<arxiv:announce_type> repeats the type. 'replace' and 'replace-cross' are revisions of older
papers and are skipped (assumption: only 'new' and 'cross' are news)."""

from __future__ import annotations

import re

from collectors.base import TEXT_MAX, Collected
from collectors.feedparse import parse_feed
from collectors.http import Fetcher, FetchError
from collectors.rss import entry_to_item
from collectors.text import clean_text
from models.sources import SourceEntry

KEEP_TYPES = {"new", "cross"}
_PREFIX = re.compile(r"(?is)^\s*arXiv:\S+\s+Announce Type:\s*([\w-]+)\s*Abstract:\s*(.*)$")


def parse_arxiv(source: SourceEntry, body: bytes) -> Collected:
    out = Collected()
    for e in parse_feed(body):
        out.fetched += 1
        m = _PREFIX.match(e.body)
        kind = e.extra.get("announce_type") or (m.group(1) if m else "new")
        if kind.lower() not in KEEP_TYPES:
            continue
        abstract = m.group(2) if m else e.body
        item = entry_to_item(
            source, e, text=clean_text(abstract, max_chars=TEXT_MAX, is_html=False)
        )
        if item is None:
            out.warnings.append(f"{source.id}: skipped entry without title/link")
        else:
            out.items.append(item)
    return out


class ArxivCollector:
    def __init__(self, source: SourceEntry) -> None:
        self.source = source

    def fetch(self, fetcher: Fetcher) -> Collected:
        try:
            body = fetcher.get(self.source.url or "", per_min=self.source.rate_limit_per_min)
            res = parse_arxiv(self.source, body)
            if res.fetched == 0:
                res.warnings.append(f"{self.source.id}: response had 0 entries ({len(body)} bytes)")
            return res
        except (FetchError, ValueError) as exc:
            return Collected(warnings=[f"{self.source.id}: {exc}"], failed=True)
