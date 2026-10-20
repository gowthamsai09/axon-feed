"""Redirect resolution (spec 5.2 step 2): Fetcher.resolve, resolve_redirects, process() wiring."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from collectors.base import RawItem
from collectors.http import MAX_REDIRECT_HOPS, Fetcher, FetchError, Throttle, make_client
from models.loaders import load_sources, load_taxonomy
from pipeline.redirects import needs_resolution, resolve_redirects
from pipeline.run import process
from pipeline.tag import Tagger

ROOT = Path(__file__).resolve().parent.parent
SOURCES = {s.id: s for s in load_sources(ROOT / "config/sources.yaml").sources}
TAGGER = Tagger(load_taxonomy(ROOT / "config/taxonomy.yaml"))
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


class Site:
    """A scripted web: redirects by URL, robots.txt per host, and a log of requested URLs."""

    def __init__(self, redirects: dict[str, str], robots: dict[str, str] | None = None) -> None:
        self.redirects = redirects
        self.robots = robots or {}
        self.log: list[str] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        self.log.append(url)
        if req.url.path == "/robots.txt":
            body = self.robots.get(req.url.host)
            return httpx.Response(200, text=body) if body else httpx.Response(404)
        if url in self.redirects:
            return httpx.Response(301, headers={"Location": self.redirects[url]})
        return httpx.Response(200, content=b"<html>never read</html>")

    def fetcher(self) -> Fetcher:
        client = httpx.Client(transport=httpx.MockTransport(self))
        return Fetcher(client, Throttle(sleep=lambda _s: None))


def test_resolve_follows_a_chain_and_returns_the_final_url() -> None:
    site = Site(
        {
            "https://t.co/abc": "https://bit.ly/xyz",
            "https://bit.ly/xyz": "https://example.com/post?utm_source=tw",
        }
    )
    final = site.fetcher().resolve("https://t.co/abc", per_min=60)
    assert final == "https://example.com/post?utm_source=tw"
    # the last hop is requested once (to see it is not another redirect) and no body is read
    assert site.log.count("https://example.com/post?utm_source=tw") == 1
    assert site.log[0] == "https://t.co/robots.txt" and len(site.log) == 6


def test_resolve_joins_relative_locations_and_handles_no_redirect() -> None:
    site = Site({"https://bit.ly/a": "/b/c"})
    assert site.fetcher().resolve("https://bit.ly/a", per_min=60) == "https://bit.ly/b/c"
    assert site.fetcher().resolve("https://example.com/p", per_min=60) == "https://example.com/p"


def test_resolve_stops_on_loops_and_long_chains() -> None:
    loop = Site({"https://bit.ly/a": "https://bit.ly/b", "https://bit.ly/b": "https://bit.ly/a"})
    with pytest.raises(FetchError, match="redirects"):
        loop.fetcher().resolve("https://bit.ly/a", per_min=60)
    assert len([u for u in loop.log if not u.endswith("robots.txt")]) == MAX_REDIRECT_HOPS + 1


def test_resolve_refuses_a_hop_to_a_non_public_host() -> None:
    for target in ("http://169.254.169.254/latest", "http://localhost:8080/x", "http://10.0.0.5/"):
        site = Site({"https://bit.ly/a": target})
        with pytest.raises(FetchError, match="non-public|http"):
            site.fetcher().resolve("https://bit.ly/a", per_min=60)
        assert target not in site.log


def test_real_client_guard_never_requests_a_host_that_resolves_privately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_getaddrinfo(host: str, *_a: object, **_k: object) -> list[tuple[object, ...]]:
        ip = "10.0.0.9" if host == "internal.example" else "93.184.216.34"
        return [(2, 1, 6, "", (ip, 0))]

    monkeypatch.setattr("collectors.http.socket.getaddrinfo", fake_getaddrinfo)
    site = Site({"https://bit.ly/a": "https://internal.example/x"})
    client = make_client("https://x", transport=httpx.MockTransport(site))
    Fetcher(client, Throttle(sleep=lambda _s: None)).resolve("https://bit.ly/a", per_min=60)
    assert not any("internal.example" in u for u in site.log)  # the guard stopped every request


def test_robots_on_the_first_url_blocks_but_on_a_later_hop_still_returns_it() -> None:
    first = Site(
        {"https://t.co/a": "https://example.com/p"}, {"t.co": "User-agent: *\nDisallow: /"}
    )
    with pytest.raises(FetchError, match="robots"):
        first.fetcher().resolve("https://t.co/a", per_min=60)
    later = Site(
        {"https://bit.ly/a": "https://example.com/p"}, {"example.com": "User-agent: *\nDisallow: /"}
    )
    assert later.fetcher().resolve("https://bit.ly/a", per_min=60) == "https://example.com/p"


def test_unreachable_server_is_a_fetch_error() -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        raise httpx.ConnectError("down", request=req)

    f = Fetcher(httpx.Client(transport=httpx.MockTransport(boom)), Throttle(sleep=lambda _s: None))
    with pytest.raises(FetchError, match="ConnectError"):
        f.resolve("https://bit.ly/a", per_min=60)


def hn(url: str, title: str = "A post") -> RawItem:
    return RawItem(
        source_id="hn-ai", source_type="api", url=url, title=title, published_at=NOW, text=""
    )


def test_needs_resolution_only_for_listed_hosts() -> None:
    assert needs_resolution("https://t.co/abc") and needs_resolution("https://BIT.LY/a")
    assert needs_resolution("https://www.bit.ly/a") and needs_resolution("https://t.co./abc")
    assert not needs_resolution("https://example.com/post")
    assert not needs_resolution("https://notbit.ly/a") and not needs_resolution("https://x.co/a")


def test_resolve_redirects_maps_only_listed_urls() -> None:
    site = Site({"https://bit.ly/a": "https://example.com/post"})
    raws = [
        hn("https://bit.ly/a"),
        hn("https://bit.ly/a"),  # same URL twice: one request
        hn("https://example.com/direct"),
        hn("https://tinyurl.com/same"),  # no redirect: not in the mapping
    ]
    resolved, warnings = resolve_redirects(raws, site.fetcher(), {"hn-ai": 60})
    assert resolved == {  # a short link that does not redirect maps to itself (cached, not retried)
        "https://bit.ly/a": "https://example.com/post",
        "https://tinyurl.com/same": "https://tinyurl.com/same",
    }
    assert warnings == []
    assert sum(1 for u in site.log if u == "https://bit.ly/a") == 1
    assert not any("example.com/direct" in u for u in site.log)


def test_resolve_redirects_failures_warn_and_keep_the_original() -> None:
    site = Site(
        {"https://bit.ly/a": "ftp://example.com/file", "https://ow.ly/b": "http://10.0.0.1/"}
    )
    resolved, warnings = resolve_redirects(
        [hn("https://bit.ly/a"), hn("https://ow.ly/b")], site.fetcher(), {}
    )
    assert resolved == {}
    assert len(warnings) == 2 and all(w.startswith("redirect hn-ai: ") for w in warnings)


def test_resolve_redirects_respects_the_cap() -> None:
    site = Site({f"https://bit.ly/{i}": f"https://example.com/{i}" for i in range(5)})
    raws = [hn(f"https://bit.ly/{i}") for i in range(5)]
    resolved, warnings = resolve_redirects(raws, site.fetcher(), {}, max_fetches=2)
    assert len(resolved) == 2
    assert warnings == ["redirects: stopped after 2 resolutions"]


def test_process_merges_a_short_link_with_the_direct_article() -> None:
    direct = RawItem(
        source_id="hf-blog",
        source_type="rss",
        url="https://example.com/post?utm_source=newsletter",
        title="Qwen3 Coder gets a long context update for local inference",
        published_at=NOW,
        text="",
    )
    short = hn("https://bit.ly/a", "Interesting read")  # unrelated title: only the URL can merge
    before = process([direct, short], SOURCES, TAGGER, now=NOW, window_hours=24)
    assert len(before.items) == 2
    merged = process(
        [direct, short],
        SOURCES,
        TAGGER,
        now=NOW,
        window_hours=24,
        redirects={"https://bit.ly/a": "https://example.com/post"},
    )
    (item,) = merged.items
    assert item.canonical_url == "https://example.com/post" and item.source_id == "hf-blog"
    assert [a.source_id for a in item.also_covered_by] == ["hn-ai"]


def test_process_keeps_the_original_url_field() -> None:
    res = process(
        [hn("https://bit.ly/a")],
        SOURCES,
        TAGGER,
        now=NOW,
        window_hours=24,
        redirects={"https://bit.ly/a": "https://example.com/post"},
    )
    (item,) = res.items
    assert item.url == "https://bit.ly/a" and item.canonical_url == "https://example.com/post"


def test_resolve_redirects_skips_urls_already_in_the_cache() -> None:
    site = Site({"https://bit.ly/a": "https://example.com/post"})
    known = {"https://bit.ly/a": "https://example.com/post"}
    resolved, warnings = resolve_redirects(
        [hn("https://bit.ly/a"), hn("https://bit.ly/b")], site.fetcher(), {}, known=known
    )
    assert resolved == {"https://bit.ly/b": "https://bit.ly/b"} and warnings == []
    assert not any(u.endswith("/a") for u in site.log)
