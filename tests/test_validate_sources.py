from __future__ import annotations

import httpx

from collectors.http import Robots, Throttle
from models.sources import SourceEntry
from scripts.validate_sources import Row, count_items, probe, render_table

RSS = b"<rss><channel><item><title>a</title></item><item/></channel></rss>"
ATOM = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry/></feed>'


def entry(url: str = "https://feeds.test/f.xml", kind: str = "rss") -> SourceEntry:
    return SourceEntry.model_validate(
        {
            "id": "t",
            "type": kind,
            "url": url,
            "authority": 0.5,
            "default_tags": [],
            "rate_limit_per_min": 60,
        }
    )


def run(
    handler: httpx.MockTransport, e: SourceEntry, cache: dict[str, dict[str, str]] | None = None
) -> Row:
    with httpx.Client(transport=handler, follow_redirects=True) as c:
        return probe(
            c, Robots(c), Throttle(sleep=lambda _s: None), cache or {}, e, "t", e.url or ""
        )


def make(
    routes: dict[str, httpx.Response], robots: str = "User-agent: *\nAllow: /"
) -> httpx.MockTransport:
    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text=robots)
        return routes.get(str(req.url), httpx.Response(404))

    return httpx.MockTransport(h)


def test_count_items() -> None:
    assert count_items("rss", RSS) == 2
    assert count_items("rss", ATOM) == 1
    assert count_items("api", b'{"hits":[1,2,3]}') == 3
    assert count_items("api", b"[1,2]") == 2


def test_count_items_rejects_garbage_and_xml_bombs() -> None:
    for kind, body in (("rss", b"<not xml"), ("api", b"{nope")):
        try:
            count_items(kind, body)
        except ValueError:
            continue
        raise AssertionError("expected ValueError")
    bomb = b'<!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;">]><x>&b;</x>'
    try:
        count_items("rss", bomb)
    except ValueError:
        return
    raise AssertionError("entity declarations must be rejected")


def test_ok() -> None:
    r = run(
        make({"https://feeds.test/f.xml": httpx.Response(200, content=RSS, headers={"etag": "x"})}),
        entry(),
    )
    assert (r.status, r.items, r.http) == ("OK", 2, 200) and "etag=yes" in r.note


def test_redirected() -> None:
    routes = {
        "https://feeds.test/f.xml": httpx.Response(
            301, headers={"location": "https://feeds.test/g.xml"}
        ),
        "https://feeds.test/g.xml": httpx.Response(200, content=RSS),
    }
    r = run(make(routes), entry())
    assert r.status == "redirected" and r.final_url == "https://feeds.test/g.xml"


def test_empty_failed_and_unparsable() -> None:
    assert (
        run(
            make({"https://feeds.test/f.xml": httpx.Response(200, content=b"<rss/>")}), entry()
        ).status
        == "empty"
    )
    assert run(make({"https://feeds.test/f.xml": httpx.Response(503)}), entry()).status == "failed"
    assert (
        run(make({"https://feeds.test/f.xml": httpx.Response(200, content=b"<<")}), entry()).status
        == "failed"
    )


def test_blocked_by_robots() -> None:
    t = make(
        {"https://feeds.test/f.xml": httpx.Response(200, content=RSS)},
        robots="User-agent: *\nDisallow: /",
    )
    assert run(t, entry()).status == "blocked-by-robots"


def test_robots_5xx_is_disallow_and_4xx_is_allow() -> None:
    def h5(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500 if req.url.path == "/robots.txt" else 200, content=RSS)

    assert run(httpx.MockTransport(h5), entry()).status == "blocked-by-robots"

    def h4(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404 if req.url.path == "/robots.txt" else 200, content=RSS)

    assert run(httpx.MockTransport(h4), entry()).status == "OK"


def test_conditional_request_304() -> None:
    seen: dict[str, str] = {}

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        seen.update(req.headers)
        return httpx.Response(304)

    cache = {"https://feeds.test/f.xml": {"etag": '"abc"', "last_modified": ""}}
    r = run(httpx.MockTransport(h), entry(), cache)
    assert r.status == "OK" and r.http == 304 and seen["if-none-match"] == '"abc"'


def test_throttle_sleeps_between_same_host_requests() -> None:
    now = [0.0]
    slept: list[float] = []
    t = Throttle(clock=lambda: now[0], sleep=slept.append)
    t.wait("h", 4)
    now[0] = 5.0
    t.wait("h", 4)
    assert slept == [10.0]
    t.wait("other", 4)
    assert slept == [10.0]


def test_render_table() -> None:
    out = render_table(
        [Row("a", "u", "OK", 200, 3), Row("b", "u", "failed", None, None, None, "boom")]
    )
    assert "SOURCE" in out and "boom" in out
