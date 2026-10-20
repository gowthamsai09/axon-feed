"""Redirect resolution for known redirector hosts (spec 5.2 step 2).

Short links (t.co, bit.ly, feed proxies) hide the real article URL, so the same article arriving
from two sources would get two ids and never merge. Only hosts in REDIRECT_HOSTS are resolved: one
request per such item, nothing for ordinary article links. Failures keep the original URL and add a
warning; they never fail the run. `canonicalize_url` itself stays pure."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from urllib.parse import urlsplit

from collectors.base import RawItem
from collectors.http import Fetcher, FetchError
from pipeline.canonicalize import canonicalize_url

# Assumption: extend from real feed output (scripts.inspect_feed / the host listing in the notes).
REDIRECT_HOSTS = frozenset(
    {
        "t.co",
        "bit.ly",
        "tinyurl.com",
        "ow.ly",
        "buff.ly",
        "lnkd.in",
        "goo.gl",
        "dlvr.it",
        "feedproxy.google.com",
    }
)
DEFAULT_MAX_RESOLVES = 30


def needs_resolution(url: str) -> bool:
    try:
        host = (urlsplit(url).hostname or "").rstrip(".").lower()
    except ValueError:
        return False
    return any(host == h or host.endswith("." + h) for h in REDIRECT_HOSTS)


def resolve_redirects(
    raws: Iterable[RawItem],
    fetcher: Fetcher,
    per_min: dict[str, float],
    *,
    max_fetches: int = DEFAULT_MAX_RESOLVES,
    known: Mapping[str, str] | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Map original url -> final url for items on redirector hosts (at most max_fetches).
    URLs already in `known` (the cache from earlier runs) are not fetched again and are not in
    the result. A URL that does not redirect maps to itself, so it is not retried next run."""
    resolved: dict[str, str] = {}
    warnings: list[str] = []
    seen: set[str] = set()
    fetches = 0
    for raw in raws:
        url = raw.url
        if url in seen or (known and url in known) or not needs_resolution(url):
            continue
        if fetches >= max_fetches:
            warnings.append(f"redirects: stopped after {max_fetches} resolutions")
            break
        seen.add(url)
        fetches += 1
        try:
            final = fetcher.resolve(url, per_min=per_min.get(raw.source_id, 6))
            canonicalize_url(final)  # must still be a usable http(s) URL
        except (FetchError, ValueError) as exc:
            warnings.append(f"redirect {raw.source_id}: {url}: {exc}")
            continue
        resolved[url] = final
    return resolved, warnings
