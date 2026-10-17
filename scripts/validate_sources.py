"""Probe sources with `verify: true`. Run on YOUR machine (needs network):

    python -m scripts.validate_sources            # from the repo root
    python -m scripts.validate_sources --all --json report.json

Respects robots.txt, sends a descriptive User-Agent, uses ETag/Last-Modified conditional
requests on repeat runs (cache in .cache/), throttles per host by rate_limit_per_min.
Does not check terms of service (spec Appendix B #11).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from defusedxml import ElementTree as ET

from collectors.http import MAX_BYTES, PLACEHOLDER_CONTACT, Robots, Throttle, user_agent
from models.loaders import ConfigError, load_sources
from models.sources import SourceEntry

ACCEPT = (
    "application/atom+xml, application/rss+xml, application/xml;q=0.9, "
    "application/json;q=0.9, */*;q=0.5"
)
CONFIG = Path(__file__).resolve().parent.parent / "config" / "sources.yaml"
CACHE = Path(".cache") / "validate_sources_etags.json"


@dataclass
class Row:
    source: str
    url: str
    status: str  # OK | redirected | failed | empty | blocked-by-robots
    http: int | None = None
    items: int | None = None
    final_url: str | None = None
    note: str = ""


def count_items(kind: str, body: bytes) -> int:
    """Number of entries in a feed/API response. Raises ValueError if unparsable."""
    if kind == "api":
        try:
            data: Any = json.loads(body)
        except ValueError as exc:
            raise ValueError(f"invalid JSON: {exc}") from exc
        if isinstance(data, list):
            return len(data)
        if isinstance(data, dict):
            for key in ("hits", "items", "results", "data"):
                if isinstance(data.get(key), list):
                    return len(data[key])
            return next((len(v) for v in data.values() if isinstance(v, list)), 0)
        return 0
    try:
        root = ET.fromstring(body)
    except Exception as exc:  # defusedxml raises several types
        raise ValueError(f"invalid XML: {type(exc).__name__}") from exc
    return sum(1 for el in root.iter() if str(el.tag).rsplit("}", 1)[-1] in {"item", "entry"})


def probe(
    client: httpx.Client,
    robots: Robots,
    throttle: Throttle,
    cache: dict[str, dict[str, str]],
    entry: SourceEntry,
    label: str,
    url: str,
) -> Row:
    allowed, rnote = robots.check(url)
    if not allowed:
        return Row(label, url, "blocked-by-robots", note=rnote or "disallowed by robots.txt")
    throttle.wait(urlsplit(url).netloc, entry.rate_limit_per_min)
    headers = {"Accept": ACCEPT}
    cached = cache.get(url, {})
    if cached.get("etag"):
        headers["If-None-Match"] = cached["etag"]
    if cached.get("last_modified"):
        headers["If-Modified-Since"] = cached["last_modified"]
    try:
        with client.stream("GET", url, headers=headers) as resp:
            body = bytearray()
            for chunk in resp.iter_bytes():
                body += chunk
                if len(body) > MAX_BYTES:
                    return Row(label, url, "failed", resp.status_code, note="body over 5 MB")
            status, final = resp.status_code, str(resp.url)
            redirected = bool(resp.history)
            etag, lm = resp.headers.get("etag"), resp.headers.get("last-modified")
            retry = resp.headers.get("retry-after")
    except httpx.HTTPError as exc:
        return Row(label, url, "failed", note=f"{type(exc).__name__}: {exc}"[:120])
    cond = f"etag={'yes' if etag else 'no'} last-modified={'yes' if lm else 'no'}"
    fin = final if final != url else None
    if etag or lm:
        cache[url] = {"etag": etag or "", "last_modified": lm or ""}
    if status == 304:
        return Row(label, url, "OK", 304, final_url=fin, note="304 Not Modified; conditional OK")
    if status >= 400 or status < 200 or status >= 300:
        extra = f" retry-after={retry}" if retry else ""
        return Row(label, url, "failed", status, final_url=fin, note=f"HTTP {status}{extra}")
    try:
        n = count_items(entry.type, bytes(body))
    except ValueError as exc:
        return Row(label, url, "failed", status, final_url=fin, note=str(exc))
    if n == 0:
        return Row(label, url, "empty", status, 0, fin, cond)
    return Row(label, url, "redirected" if redirected else "OK", status, n, fin, cond)


def render_table(rows: list[Row]) -> str:
    head = ("SOURCE", "STATUS", "HTTP", "ITEMS", "NOTE")
    data = [
        (r.source, r.status, str(r.http or "-"), "-" if r.items is None else str(r.items), r.note)
        for r in rows
    ]
    widths = [max(len(x[i]) for x in [head, *data]) for i in range(4)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths) + "  {}"
    lines = [fmt.format(*head), fmt.format(*("-" * w for w in widths), "-" * 4)]
    lines += [fmt.format(*d) for d in data]
    moved = [f"  {r.source}: {r.url} -> {r.final_url}" for r in rows if r.final_url]
    if moved:
        lines += ["", "Final URLs after redirects:", *moved]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--config", type=Path, default=CONFIG)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--contact", default=os.environ.get("AXON_CONTACT_URL", PLACEHOLDER_CONTACT))
    ap.add_argument("--all", action="store_true", help="probe sources even if verify is false")
    ap.add_argument("--only", help="probe only this source id")
    ap.add_argument("--json", type=Path, help="also write the rows as JSON")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--strict", action="store_true", help="exit 1 if any row is not OK")
    args = ap.parse_args(argv)
    try:
        cfg = load_sources(args.config)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    entries = [s for s in cfg.sources if (args.all or s.verify) and args.only in (None, s.id)]
    cache: dict[str, dict[str, str]] = {}
    if not args.no_cache and CACHE.exists():
        cache = json.loads(CACHE.read_text(encoding="utf-8"))
    rows: list[Row] = []
    with httpx.Client(
        headers={"User-Agent": user_agent(args.contact)},
        timeout=httpx.Timeout(args.timeout),
        follow_redirects=True,
    ) as client:
        robots, throttle = Robots(client), Throttle()
        for entry in entries:
            for label, url in entry.expand_urls():
                row = probe(client, robots, throttle, cache, entry, label, url)
                print(f"  {row.status:<18} {label}", file=sys.stderr, flush=True)
                rows.append(row)
    if not args.no_cache:
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    print(render_table(rows))
    counts = {s: sum(r.status == s for r in rows) for s in sorted({r.status for r in rows})}
    print(f"\nSummary: {counts}")
    if args.json:
        args.json.write_text(json.dumps([asdict(r) for r in rows], indent=2), encoding="utf-8")
    return 1 if args.strict and any(r.status != "OK" for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
