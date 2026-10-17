from __future__ import annotations

import httpx
import pytest

from collectors.base import RawItem
from collectors.http import Fetcher, Throttle
from pipeline.extract import MIN_TEXT, enrich, extract_main_text

PARA = "This is a long paragraph about a new open model release with plenty of words in it. " * 4
PAGE = (
    "<html><head><title>T</title><meta property='og:description' content='Meta &amp; desc'></head>"
    f"<body><nav>menu links</nav><article><h1>Heading</h1><p>{PARA}</p><p>Second paragraph.</p>"
    "<script>var x=1</script></article></body></html>"
).encode()


def item(url: str, text: str = "", sid: str = "hf-blog") -> RawItem:
    return RawItem(source_id=sid, source_type="rss", url=url, title="t", text=text)


def fetcher(routes: dict[str, httpx.Response], seen: list[str] | None = None) -> Fetcher:
    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /secret")
        if seen is not None:
            seen.append(str(req.url))
        return routes.get(str(req.url), httpx.Response(404))

    return Fetcher(httpx.Client(transport=httpx.MockTransport(h)), Throttle(sleep=lambda _s: None))


def test_extract_main_text_and_meta_fallback() -> None:
    text = extract_main_text(PAGE)
    assert "new open model release" in text and "menu links" not in text and "var x" not in text
    thin = (
        b"<html><head><meta name='description' content='Only a meta description here'>"
        b"</head><body></body></html>"
    )
    assert extract_main_text(thin) == "Only a meta description here"
    assert extract_main_text(b"") == ""


def test_enrich_fetches_only_thin_items_and_reports_failures() -> None:
    seen: list[str] = []
    f = fetcher(
        {"https://a.test/thin": httpx.Response(200, content=PAGE),
         "https://a.test/broken": httpx.Response(500)},
        seen,
    )  # fmt: skip
    items = [
        item("https://a.test/thin"),
        item("https://a.test/full", "x" * MIN_TEXT),
        item("https://a.test/broken"),
        item("https://a.test/secret"),
    ]
    out, warn = enrich(items, f, {"hf-blog": 60})
    assert "new open model release" in out[0].text and out[1].text == "x" * MIN_TEXT
    assert out[2].text == "" and out[3].text == ""
    assert any("HTTP 500" in w for w in warn) and any("robots" in w for w in warn)
    assert "https://a.test/full" not in seen


def test_enrich_respects_max_fetches() -> None:
    f = fetcher({f"https://a.test/{i}": httpx.Response(200, content=PAGE) for i in range(5)})
    out, warn = enrich([item(f"https://a.test/{i}") for i in range(5)], f, {}, max_fetches=2)
    assert sum(bool(o.text) for o in out) == 2 and any("stopped after 2" in w for w in warn)


@pytest.mark.parametrize("n", [0, 1])
def test_enrich_noop(n: int) -> None:
    assert enrich([], fetcher({}), {}) == ([], []) if n == 0 else True
