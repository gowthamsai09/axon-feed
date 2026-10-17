"""GitHub releases.atom per repo. Verified against real captures (2026-10-16): Atom entries with
a bare tag as title ('v0.40.5', 'b11685'), <updated> only (no <published>), HTML <content>
that can be very large and embeds signed image URLs and contributor emails (both stripped by
clean_text). Titles get the repo name prefixed. Pre-release tags (rc, alpha, beta, dev, ...) are
skipped (decision: release candidates are noise in a daily digest)."""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

from collectors.base import TEXT_MAX, Collected
from collectors.feedparse import parse_feed
from collectors.http import Fetcher, FetchError
from collectors.rss import entry_to_item
from collectors.text import clean_text
from models.sources import SourceEntry

_PRE = re.compile(r"(?i)(?:^|[-_.\d])(?:rc|alpha|beta|dev|pre|preview|nightly|snapshot)[-_.]?\d*$")


def tag_from_link(link: str) -> str:
    return unquote(urlsplit(link).path.rsplit("/", 1)[-1])


def is_prerelease(tag: str) -> bool:
    return bool(_PRE.search(tag))


def parse_releases(source: SourceEntry, repo: str, body: bytes) -> Collected:
    out = Collected()
    name = repo.split("/", 1)[1].lower()
    for e in parse_feed(body):
        out.fetched += 1
        if is_prerelease(tag_from_link(e.link)) or is_prerelease(e.title):
            continue
        title = e.title if name in e.title.lower() else f"{repo} {e.title}"
        text = clean_text(e.body, max_chars=TEXT_MAX, is_html=e.body_is_html)
        item = entry_to_item(source, e, title=title, text=text)
        if item is None:
            out.warnings.append(f"{source.id}[{repo}]: skipped entry without title/link")
        else:
            out.items.append(item)
    return out


class GithubReleasesCollector:
    def __init__(self, source: SourceEntry) -> None:
        self.source = source

    def fetch(self, fetcher: Fetcher) -> Collected:
        out = Collected()
        for label, url in self.source.expand_urls():
            repo = label.split("[", 1)[1].rstrip("]")
            try:
                body = fetcher.get(url, per_min=self.source.rate_limit_per_min)
                part = parse_releases(self.source, repo, body)
            except (FetchError, ValueError) as exc:
                out.warnings.append(f"{label}: {exc}")
                continue
            out.items += part.items
            out.warnings += part.warnings
            out.fetched += part.fetched
        out.failed = out.fetched == 0 and bool(out.warnings)
        return out
