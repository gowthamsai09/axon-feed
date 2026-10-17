"""Polite, SSRF-guarded HTTP for collectors (spec 5.1, 10.1): User-Agent, robots.txt,
per-host throttle, size cap, public-hosts-only (also across redirects)."""

from __future__ import annotations

import ipaddress
import socket
import time
from collections.abc import Callable
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

# TODO: replace with a real contact URL before running regularly (spec 5.1).
PLACEHOLDER_CONTACT = "https://github.com/gowthamsai09/Axon"
UA_TOKEN = "AxonFeedBot"
MAX_BYTES = 5_000_000


def user_agent(contact: str) -> str:
    return f"{UA_TOKEN}/0.1 (+{contact})"


class FetchError(Exception):
    """A fetch that should be logged and skipped, never fail the run (spec 5.2 step 1)."""


def assert_public_url(url: str, *, resolve: bool = True) -> None:
    """Raise ValueError unless url is http(s) to a public host (no loopback/private/link-local)."""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError(f"not an http(s) URL: {url!r}")
    if parts.username or parts.password:
        raise ValueError("credentials in URL")
    host = parts.hostname.rstrip(".").lower()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError(f"non-public host: {host}")
    candidates: list[str] = []
    try:
        ipaddress.ip_address(host)
        candidates = [host]
    except ValueError:
        if resolve:
            try:
                candidates = [str(i[4][0]) for i in socket.getaddrinfo(host, None)]
            except OSError:
                # Behind a corporate proxy, names resolve only at the proxy. An unresolvable host
                # cannot be connected to directly anyway, so only block hosts that DO resolve to
                # a non-public address.
                candidates = []
    for c in candidates:
        ip = ipaddress.ip_address(c.split("%")[0])
        if not ip.is_global:
            raise ValueError(f"non-public address {ip} for {host}")


def make_client(
    contact: str,
    *,
    timeout: float = 20.0,
    resolve: bool = True,
    transport: httpx.BaseTransport | None = None,
) -> httpx.Client:
    """Client whose every request, including each redirect hop, passes assert_public_url."""

    def guard(request: httpx.Request) -> None:
        try:
            assert_public_url(str(request.url), resolve=resolve)
        except ValueError as exc:
            raise httpx.RequestError(f"blocked by SSRF guard: {exc}", request=request) from exc

    return httpx.Client(
        headers={"User-Agent": user_agent(contact)},
        timeout=httpx.Timeout(timeout),
        follow_redirects=True,
        max_redirects=5,
        event_hooks={"request": [guard]},
        transport=transport,  # tests inject one; httpx then ignores system proxy settings
    )


class Throttle:
    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._last: dict[str, float] = {}
        self._clock, self._sleep = clock, sleep

    def wait(self, host: str, per_min: float) -> None:
        gap = 60.0 / per_min
        last = self._last.get(host)
        if last is not None:
            remaining = gap - (self._clock() - last)
            if remaining > 0:
                (self._sleep or time.sleep)(remaining)
        self._last[host] = self._clock()


class Robots:
    """robots.txt per origin. 4xx = allow, 5xx/unreachable = disallow (RFC 9309)."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client
        self._cache: dict[str, tuple[RobotFileParser, str]] = {}

    def check(self, url: str) -> tuple[bool, str]:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._cache:
            self._cache[origin] = self._load(origin)
        parser, note = self._cache[origin]
        return parser.can_fetch(UA_TOKEN, url), note

    def _load(self, origin: str) -> tuple[RobotFileParser, str]:
        parser = RobotFileParser()
        try:
            resp = self._client.get(f"{origin}/robots.txt")
        except httpx.HTTPError as exc:
            parser.parse(["User-agent: *", "Disallow: /"])
            reason = f"{type(exc).__name__}: {exc}"[:120]
            return parser, f"robots.txt unreachable ({reason}): treated as disallow"
        if resp.status_code == 200:
            parser.parse(resp.text.splitlines())
            return parser, ""
        if 400 <= resp.status_code < 500:
            parser.parse([])
            return parser, f"robots.txt HTTP {resp.status_code}: treated as allow"
        parser.parse(["User-agent: *", "Disallow: /"])
        return parser, f"robots.txt HTTP {resp.status_code}: treated as disallow"


class Fetcher:
    """What collectors and the extractor use to GET bytes."""

    def __init__(self, client: httpx.Client, throttle: Throttle | None = None) -> None:
        self.client = client
        self.robots = Robots(client)
        self.throttle = throttle or Throttle()

    def get(self, url: str, *, per_min: float, params: dict[str, str | int] | None = None) -> bytes:
        full = str(httpx.URL(url).copy_merge_params(params or {}))
        allowed, note = self.robots.check(full)
        if not allowed:
            raise FetchError(f"blocked by robots.txt{': ' + note if note else ''}")
        self.throttle.wait(urlsplit(full).netloc, per_min)
        try:
            with self.client.stream("GET", full, headers={"Accept": "*/*"}) as resp:
                if resp.status_code != 200:
                    raise FetchError(f"HTTP {resp.status_code}")
                body = bytearray()
                for chunk in resp.iter_bytes():
                    body += chunk
                    if len(body) > MAX_BYTES:
                        raise FetchError("body over 5 MB")
                return bytes(body)
        except httpx.HTTPError as exc:
            raise FetchError(f"{type(exc).__name__}: {exc}"[:200]) from exc
