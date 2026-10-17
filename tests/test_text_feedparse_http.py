from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from collectors.feedparse import parse_date, parse_feed
from collectors.http import Fetcher, FetchError, Throttle, assert_public_url, make_client
from collectors.text import clean_text, html_to_text, truncate
from scripts.capture_samples import sanitize, shrink_content


def test_html_to_text_blocks_entities_and_scripts() -> None:
    out = html_to_text(
        "<p>One &amp; two</p><script>alert(1)</script><li>Three</li><style>x{}</style>"
    )
    assert out.split() == ["One", "&", "two", "Three"]


def test_clean_text_strips_urls_emails_and_noise_lines() -> None:
    raw = (
        "<p>Fix bug see https://evil.example/x?jwt=abc.def and www.x.example</p>"
        "<p>Co-authored-by: A B <a.b@corp.example></p><p>Signed-off-by: C</p>"
        "<p>Thanks @user (user@corp.example)!</p>"
    )
    out = clean_text(raw, max_chars=500)
    assert out == "Fix bug see and Thanks @user ()!"
    assert "jwt" not in out and "@corp" not in out


def test_clean_text_plain_mode_keeps_angle_brackets() -> None:
    assert clean_text("if x < y and y > z", max_chars=100, is_html=False) == "if x < y and y > z"


def test_truncate() -> None:
    assert truncate("short", 10) == "short"
    out = truncate("word " * 100, 50)
    assert len(out) <= 50 and out.endswith("\u2026")
    assert truncate("x" * 100, 10).endswith("\u2026")


def test_parse_date_variants() -> None:
    assert parse_date("Fri, 16 Oct 2026 00:00:00 -0400") == datetime(2026, 10, 16, 4, 0, tzinfo=UTC)
    assert parse_date("2026-10-15T19:18:39Z") == datetime(2026, 10, 15, 19, 18, 39, tzinfo=UTC)
    assert parse_date("2026-10-15T19:18:39") == datetime(2026, 10, 15, 19, 18, 39, tzinfo=UTC)
    assert parse_date("not a date") is None and parse_date("") is None and parse_date(None) is None


BOMB = (
    b'<!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;">]>'
    b"<rss><item><title>&b;</title></item></rss>"
)


def test_parse_feed_rss_and_atom_and_garbage() -> None:
    rss = (
        b'<rss xmlns:dc="http://purl.org/dc/elements/1.1/"><channel>'
        b"<item><title> A &amp;amp; B </title>"
        b"<link>https://x.test/a</link><description>d</description>"
        b"<pubDate>Fri, 16 Oct 2026 00:00:00 GMT"
        b"</pubDate><dc:creator>Zed</dc:creator><category>c1</category></item></channel></rss>"
    )
    [e] = parse_feed(rss)
    assert (e.title, e.link, e.author, e.categories) == ("A & B", "https://x.test/a", "Zed", ["c1"])
    atom = (
        b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>v1</title>'
        b'<link rel="alternate" href="https://x.test/r"/><updated>2026-10-15T01:00:00Z</updated>'
        b'<content type="html">&lt;p&gt;hi&lt;/p&gt;</content>'
        b"<author><name>Bob</name></author></entry></feed>"
    )
    [a] = parse_feed(atom)
    assert (a.link, a.author, a.date) == (
        "https://x.test/r",
        "Bob",
        datetime(2026, 10, 15, 1, tzinfo=UTC),
    )
    assert a.body == "<p>hi</p>" and a.body_is_html
    with pytest.raises(ValueError):
        parse_feed(b"<not xml")
    with pytest.raises(ValueError):  # entity expansion is refused
        parse_feed(BOMB)


@pytest.mark.parametrize(
    "url",
    ["http://localhost/x", "http://127.0.0.1/", "http://10.0.0.5/a", "http://169.254.169.254/latest",
     "http://[::1]/", "http://service.internal/", "ftp://example.com/", "https://u:p@example.com/"],
)  # fmt: skip
def test_assert_public_url_rejects(url: str) -> None:
    with pytest.raises(ValueError):
        assert_public_url(url, resolve=False)


def test_assert_public_url_accepts_and_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    assert_public_url("https://example.com/x", resolve=False)
    assert_public_url("http://8.8.8.8/", resolve=False)
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.1.2.3", 0))])
    with pytest.raises(ValueError, match="non-public"):
        assert_public_url("https://rebind.example/", resolve=True)


def _fetcher(handler: httpx.MockTransport) -> Fetcher:
    client = httpx.Client(transport=handler, follow_redirects=True)
    return Fetcher(client, Throttle(sleep=lambda _s: None))


def test_fetcher_ok_robots_status_and_size() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private")
        if req.url.path == "/big":
            return httpx.Response(200, content=b"x" * 5_000_001)
        return httpx.Response(200 if req.url.path == "/ok" else 500, content=b"hi")

    f = _fetcher(httpx.MockTransport(h))
    assert f.get("https://a.test/ok", per_min=60) == b"hi"
    for path, msg in (("/private", "robots"), ("/bad", "HTTP 500"), ("/big", "5 MB")):
        with pytest.raises(FetchError, match=msg):
            f.get(f"https://a.test{path}", per_min=60)


def test_fetcher_merges_params() -> None:
    seen: list[str] = []

    def h(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        return httpx.Response(404 if req.url.path == "/robots.txt" else 200, content=b"{}")

    _fetcher(httpx.MockTransport(h)).get("https://a.test/s?q=LLM", per_min=60, params={"page": 2})
    assert any("q=LLM" in u and "page=2" in u for u in seen)


def test_ssrf_guard_blocks_redirect_to_private_host() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        if req.url.host == "public.test":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest"})
        return httpx.Response(200, content=b"secret")

    client = make_client("https://c.test", resolve=False, transport=httpx.MockTransport(h))
    with pytest.raises(httpx.RequestError, match="SSRF"):
        client.get("https://public.test/start")


def test_sanitize_and_shrink() -> None:
    raw = 'a\r\nb <img src="https://x/y.png?jwt=eyJabc.def-ghi_jk&w=1"> me@corp.example'
    out = sanitize(raw)
    assert "\r" not in out and "eyJ" not in out and "jwt=REDACTED" in out and "me@" not in out
    xml = "<content type='html'>" + ("word " * 5000) + "&amp;</content>"
    assert len(shrink_content(xml, 100)) < 200
    assert shrink_content("<content>short</content>", 100) == "<content>short</content>"


def test_unresolvable_host_is_not_blocked_but_private_resolution_is(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Behind a proxy, local DNS fails while the proxy resolves: do not refuse those hosts."""

    def no_dns(*_a: object, **_k: object) -> None:
        raise OSError("Name or service not known")

    monkeypatch.setattr("socket.getaddrinfo", no_dns)
    assert_public_url("https://only-the-proxy-can-resolve.example/feed", resolve=True)
    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("192.168.1.9", 0))])
    with pytest.raises(ValueError, match="non-public"):
        assert_public_url("https://evil.example/", resolve=True)


def test_robots_failure_reason_is_reported() -> None:
    def boom(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("proxy says no")

    f = Fetcher(httpx.Client(transport=httpx.MockTransport(boom)), Throttle(sleep=lambda _s: None))
    with pytest.raises(FetchError, match="proxy says no"):
        f.get("https://a.test/x", per_min=60)
