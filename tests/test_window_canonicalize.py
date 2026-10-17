from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from models.common import compute_item_id
from models.feed_item import FeedItem
from pipeline.canonicalize import canonicalize_url, item_id_for
from pipeline.window import DEV_WINDOW_HOURS, effective_date, in_window, window_start
from tests.factories import feed_item

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


def test_window_default_is_24h_in_dev() -> None:
    assert DEV_WINDOW_HOURS == 24


def test_in_window_boundaries() -> None:
    assert in_window(NOW - timedelta(hours=23, minutes=59), now=NOW, hours=24)
    assert in_window(NOW - timedelta(hours=24), now=NOW, hours=24)
    assert not in_window(NOW - timedelta(hours=24, seconds=1), now=NOW, hours=24)
    assert not in_window(NOW - timedelta(days=3), now=NOW, hours=24)
    assert in_window(NOW - timedelta(days=3), now=NOW, hours=168)
    assert in_window(NOW + timedelta(hours=2), now=NOW, hours=24)  # clock skew is tolerated
    assert in_window(None, now=NOW, hours=24)  # undated: counts as published now


def test_window_validation() -> None:
    with pytest.raises(ValueError):
        window_start(datetime(2026, 1, 1), 24)
    with pytest.raises(ValueError):
        window_start(NOW, 0)
    with pytest.raises(ValueError):
        effective_date(datetime(2026, 1, 1), NOW)
    assert effective_date(None, NOW) == (NOW, True)
    assert effective_date(NOW, NOW + timedelta(1)) == (NOW, False)


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("http://Example.COM/post/?utm_source=x&b=2&a=1#frag", "https://example.com/post?a=1&b=2"),
        ("https://example.com:443/a//b/", "https://example.com/a/b"),
        ("https://example.com:8443/a", "https://example.com:8443/a"),
        ("https://example.com", "https://example.com/"),
        ("https://example.com/p?fbclid=abc&keep=1", "https://example.com/p?keep=1"),
        ("https://example.com./x", "https://example.com/x"),
        ("https://arxiv.org/abs/2510.01234v3", "https://arxiv.org/abs/2510.01234"),
        ("https://arxiv.org/pdf/2510.01234v2.pdf", "https://arxiv.org/abs/2510.01234"),
        ("http://export.arxiv.org/abs/hep-th/9901001v1", "https://arxiv.org/abs/hep-th/9901001"),
    ],
)
def test_canonicalize(raw: str, want: str) -> None:
    assert canonicalize_url(raw) == want
    assert canonicalize_url(want) == want  # idempotent


@pytest.mark.parametrize("bad", ["ftp://x.example/a", "not a url", "https://u:p@x.example/", ""])
def test_canonicalize_rejects(bad: str) -> None:
    with pytest.raises(ValueError):
        canonicalize_url(bad)


def test_cross_listed_arxiv_papers_share_an_id() -> None:
    a = item_id_for("https://arxiv.org/abs/2510.01234v1")
    b = item_id_for("https://arxiv.org/pdf/2510.01234v2")
    assert a == b and a[0] == compute_item_id("https://arxiv.org/abs/2510.01234")


def test_canonical_output_passes_feed_item_validation() -> None:
    iid, canon = item_id_for("http://Example.com/Post/?utm_campaign=z")
    FeedItem.model_validate(feed_item(id=iid, canonical_url=canon))
