"""Main-text extraction for items whose feed gave little or no text (spec 5.2 step 3).
Verified need: hf-blog entries carry only title/link/date; some deepmind entries have an empty
description. Uses trafilatura; falls back to the page's og:description / meta description."""

from __future__ import annotations

import html as htmllib
import re

import trafilatura

from collectors.base import TEXT_MAX, RawItem
from collectors.http import Fetcher, FetchError
from collectors.text import clean_text

MIN_TEXT = 200  # feed text shorter than this triggers a page fetch
_META = re.compile(
    r"(?is)<meta\s+[^>]*?(?:property|name)\s*=\s*[\"'](?:og:description|description)[\"'][^>]*?"
    r"content\s*=\s*[\"']([^\"']*)[\"']"
)


def extract_main_text(page: bytes) -> str:
    """Plain text of the main content, or the meta description, or ''."""
    text = trafilatura.extract(page, include_comments=False, include_tables=False) or ""
    if len(text) < 80:
        m = _META.search(page.decode("utf-8", errors="replace"))
        if m:
            text = htmllib.unescape(m.group(1))
    return clean_text(text, max_chars=TEXT_MAX, is_html=False)


def enrich(
    items: list[RawItem],
    fetcher: Fetcher,
    per_min: dict[str, float],
    *,
    max_fetches: int = 30,
) -> tuple[list[RawItem], list[str]]:
    """Fetch pages for thin items (at most max_fetches per run). Failures are warnings."""
    out: list[RawItem] = []
    warnings: list[str] = []
    fetched = 0
    for item in items:
        if len(item.text) >= MIN_TEXT or fetched >= max_fetches:
            out.append(item)
            continue
        fetched += 1
        try:
            page = fetcher.get(item.url, per_min=per_min.get(item.source_id, 6))
            text = extract_main_text(page)
        except FetchError as exc:
            warnings.append(f"extract {item.source_id}: {item.url}: {exc}")
            out.append(item)
            continue
        out.append(item.model_copy(update={"text": text}) if len(text) > len(item.text) else item)
    if fetched >= max_fetches:
        warnings.append(f"extract: stopped after {max_fetches} page fetches")
    return out, warnings
