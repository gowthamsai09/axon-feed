"""Hugging Face daily papers (https://huggingface.co/api/daily_papers). Verified against a real
capture (2026-10-16): a JSON list of {paper:{id,title,summary,upvotes,authors,publishedAt,
submittedOnDailyAt,...}, title, summary, numComments, publishedAt}. Item URL is the arXiv
abstract page so the item merges with the arXiv feeds. Date = submittedOnDailyAt (the day the
paper was featured, day granularity); falls back to publishedAt. We ignore ai_summary (third-party
generated text)."""

from __future__ import annotations

import json
import re
from typing import Any

from collectors.base import TEXT_MAX, Collected, RawItem
from collectors.feedparse import parse_date
from collectors.http import Fetcher, FetchError
from collectors.text import clean_text, truncate
from models.feed_item import Signals
from models.sources import SourceEntry

_ARXIV_ID = re.compile(r"^(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})$")


def _int(v: Any) -> int:
    return v if isinstance(v, int) and v > 0 else 0


def parse_daily_papers(source: SourceEntry, body: bytes) -> Collected:
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError("expected a JSON list")
    out = Collected()
    for entry in data:
        out.fetched += 1
        if not isinstance(entry, dict):
            out.warnings.append(f"{source.id}: skipped non-object entry")
            continue
        paper = entry.get("paper")
        paper = paper if isinstance(paper, dict) else {}
        pid = str(paper.get("id", ""))
        title = " ".join(str(entry.get("title") or paper.get("title") or "").split())
        if not _ARXIV_ID.match(pid) or not title:
            out.warnings.append(f"{source.id}: skipped entry with bad id/title")
            continue
        names = [a.get("name", "") for a in paper.get("authors", []) if isinstance(a, dict)]
        date = parse_date(paper.get("submittedOnDailyAt")) or parse_date(entry.get("publishedAt"))
        summary = str(paper.get("summary") or entry.get("summary") or "")
        try:
            out.items.append(
                RawItem(
                    source_id=source.id,
                    source_type=source.type,
                    url=f"https://arxiv.org/abs/{pid}",
                    title=truncate(title, 500),
                    published_at=date,
                    author=truncate(", ".join(n for n in names[:3] if n), 200) or None,
                    text=clean_text(summary, max_chars=TEXT_MAX, is_html=False),
                    signals=Signals(
                        points=_int(paper.get("upvotes")), comments=_int(entry.get("numComments"))
                    ),
                )
            )
        except ValueError:
            out.warnings.append(f"{source.id}: skipped invalid entry {pid}")
    return out


class HfDailyPapersCollector:
    def __init__(self, source: SourceEntry) -> None:
        self.source = source

    def fetch(self, fetcher: Fetcher) -> Collected:
        try:
            body = fetcher.get(self.source.url or "", per_min=self.source.rate_limit_per_min)
            res = parse_daily_papers(self.source, body)
            if res.fetched == 0:
                res.warnings.append(f"{self.source.id}: response had 0 entries ({len(body)} bytes)")
            return res
        except (FetchError, ValueError) as exc:
            return Collected(warnings=[f"{self.source.id}: {exc}"], failed=True)
