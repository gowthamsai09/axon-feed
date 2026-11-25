"""SSRF suite for the feed's collectors (spec 10.1, Appendix C 84): the guard, the redirect
resolver and the client hook. The shared URL corpus comes from the hub (HUB_DIR, default ../Axon);
no network."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from collectors.http import Fetcher, FetchError, Throttle, assert_public_url, make_client
from tests.test_redirects import Site

ROOT = Path(__file__).resolve().parent.parent
HUB = Path(os.environ.get("HUB_DIR", str(ROOT.parent / "Axon")))
CORPUS = HUB / "corpus" / "ssrf" / "urls.json"
REQUIRED = os.environ.get("REQUIRE_HUB_CORPUS") == "1"
CASES: list[dict[str, Any]] = (
    json.loads(CORPUS.read_text(encoding="utf-8")) if CORPUS.is_file() else []
)
needs_corpus = pytest.mark.skipif(
    not CASES and not REQUIRED, reason="hub corpus not found (set HUB_DIR)"
)


def patch_dns(monkeypatch: pytest.MonkeyPatch, dns: dict[str, list[str]]) -> None:
    def fake(host: str, *_a: Any, **_k: Any) -> list[tuple[Any, ...]]:
        addresses = dns.get(host.rstrip(".").lower())
        if addresses is None:
            raise OSError("no such host")
        return [(2, 1, 6, "", (ip, 0)) for ip in addresses]

    monkeypatch.setattr("socket.getaddrinfo", fake)


@needs_corpus
def test_the_corpus_was_really_loaded() -> None:
    assert len(CASES) >= 80
    assert {c["expect"] for c in CASES} == {"allowed", "blocked"}


@needs_corpus
@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_the_guard_follows_the_corpus(
    case: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_dns(monkeypatch, case["dns"])
    if case["expect"] == "allowed":
        assert_public_url(case["url"], resolve=True)
    else:
        with pytest.raises(ValueError):
            assert_public_url(case["url"], resolve=True)


@pytest.mark.parametrize(
    "url",
    [
        "http://2130706433/",
        "http://0x7f000001/",
        "http://127.1/",
        "http://0/",
        "http://017700000001/",
    ],
)
def test_numeric_host_forms_are_refused_without_dns_even_when_not_resolving(
    url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("DNS was consulted")

    monkeypatch.setattr("socket.getaddrinfo", forbidden)
    for resolve in (True, False):
        with pytest.raises(ValueError, match="non-canonical IP address form"):
            assert_public_url(url, resolve=resolve)


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("http://:@example.com/", "credentials"),
        ("http://example.com:99999/", "invalid port"),
        ("http://exa mple.com/", "invalid host name"),
        ("http://%31%32%37.0.0.1/", "invalid host name"),
        ("http://[::ffff:7f00:1]/", "non-public address"),
        ("http://[::1/", "Invalid IPv6 URL"),
    ],
)
def test_refusals_name_the_reason(url: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        assert_public_url(url, resolve=False)


def test_a_host_that_cannot_be_resolved_stays_allowed_for_proxied_networks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_dns(monkeypatch, {})  # every lookup fails, as behind a corporate proxy
    assert_public_url("https://only-the-proxy-can-resolve.example/", resolve=True)


# ---- the redirect resolver (Fetcher.resolve) ----------------------------------------------

BAD_TARGETS = [
    "ftp://example.com/x",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "http://user:pw@example.com/",
    "//evil.example@127.0.0.1/",
    "http://2130706433/",
    "http://0x7f.1/",
    "http://[::ffff:127.0.0.1]/",
    "http://example.com:99999/",
    "http://169.254.169.254/latest/meta-data",
    "http://[::1/",
]


@pytest.mark.parametrize("target", BAD_TARGETS)
def test_resolve_refuses_unsafe_redirect_targets_and_never_requests_them(target: str) -> None:
    site = Site({"https://bit.ly/a": target})
    with pytest.raises(FetchError):
        site.fetcher().resolve("https://bit.ly/a", per_min=60)
    assert not [u for u in site.log if u.startswith(target)]


def test_resolve_follows_a_safe_chain_to_its_end() -> None:
    site = Site({"https://bit.ly/a": "https://example.com/p", "https://example.com/p": "/q"})
    assert site.fetcher().resolve("https://bit.ly/a", per_min=60) == "https://example.com/q"


# ---- Fetcher.get and the client hook ------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_an_invalid_url_is_a_fetch_error_not_a_crash() -> None:
    fetcher = Site({}).fetcher()
    for url in ("http://example.com:99999/", "http://[::1/"):
        with pytest.raises(FetchError, match="invalid URL"):
            fetcher.get(url, per_min=60)


def test_a_response_that_trickles_in_is_cut_off_by_the_total_deadline() -> None:
    clock = Clock()

    def trickle() -> Iterator[bytes]:
        for _ in range(10):
            clock.now += 10.0
            yield b"x"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=trickle())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    fetcher = Fetcher(client, Throttle(sleep=lambda _s: None), max_seconds=30.0, clock=clock)
    with pytest.raises(FetchError, match="too slow"):
        fetcher.get("https://example.com/slow", per_min=60)


def test_the_client_hook_stops_a_redirect_to_a_numeric_host_before_it_is_requested() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(302, headers={"location": "http://2130706433/x"})

    client = make_client("https://c.example", resolve=False, transport=httpx.MockTransport(handler))
    fetcher = Fetcher(client, Throttle(sleep=lambda _s: None))
    with pytest.raises(FetchError, match="SSRF"):
        fetcher.get("https://public.test/start", per_min=60)
    assert not [u for u in seen if "2130706433" in u]


def test_the_client_has_the_requested_timeout() -> None:
    client = make_client(
        "https://c.example",
        timeout=7.0,
        transport=httpx.MockTransport(lambda r: httpx.Response(200)),
    )
    assert client.timeout == httpx.Timeout(7.0)
