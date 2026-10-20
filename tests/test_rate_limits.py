"""Per-domain rate-limit rule (spec A.3): the strictest declared limit governs a host."""

from __future__ import annotations

from pathlib import Path

from collectors.http import Throttle
from collectors.registry import host_limits
from models.loaders import load_sources
from models.sources import SourceEntry

ROOT = Path(__file__).resolve().parent.parent


def source(sid: str, url: str, per_min: float) -> SourceEntry:
    return SourceEntry(
        id=sid, type="rss", url=url, authority=0.5, default_tags=[], rate_limit_per_min=per_min
    )


def test_strictest_limit_per_host_from_the_real_config() -> None:
    limits = host_limits(load_sources(ROOT / "config/sources.yaml").sources)
    assert limits["rss.arxiv.org"] == 4  # three sources, one host
    assert limits["github.com"] == 10  # six release feeds, one host
    assert limits["huggingface.co"] == 6 and limits["hn.algolia.com"] == 10


def test_two_sources_on_one_host_take_the_smaller_limit() -> None:
    srcs = [
        source("a", "https://Example.com/a.xml", 30),
        source("b", "https://example.com/b.xml", 6),
        source("c", "https://other.org/c.xml", 60),
    ]
    assert host_limits(srcs) == {"example.com": 6, "other.org": 60}


def test_throttle_applies_the_host_limit_even_to_a_faster_caller() -> None:
    now = [0.0]
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    t = Throttle(clock=lambda: now[0], sleep=sleep, host_limits={"rss.arxiv.org": 4})
    t.wait("rss.arxiv.org", 60)  # first request: no wait
    t.wait("rss.arxiv.org", 60)  # caller says 60/min, the host says 4/min: 15 s gap
    assert slept == [15.0]
    t.wait("RSS.arxiv.org", 60)  # host names are case-insensitive
    assert slept == [15.0, 15.0]


def test_hosts_without_a_declared_limit_use_the_callers_limit() -> None:
    now = [0.0]
    slept: list[float] = []
    t = Throttle(clock=lambda: now[0], sleep=slept.append, host_limits={"x.org": 1})
    t.wait("blog.example", 12)
    t.wait("blog.example", 12)
    assert slept == [5.0]
