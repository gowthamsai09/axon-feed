from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from models.common import TOPIC_SLUGS, compute_item_id
from models.feed_item import FeedItem
from tests.factories import feed_item


def test_valid_spec_example() -> None:
    item = FeedItem.model_validate(feed_item())
    assert item.tags == ["open-source-llms", "llm-releases"]


def test_optional_fields_may_be_omitted() -> None:
    data = feed_item()
    for k in ("author", "excerpt", "cluster_id", "also_covered_by", "signals", "tags"):
        data.pop(k)
    FeedItem.model_validate(data)


@pytest.mark.parametrize(
    "over",
    [
        {"id": "XYZ"},
        {"id": "9c1e4a7b2d3f5a6"},
        {"tags": ["not-a-slug"]},
        {"tags": ["rag", "rag"]},
        {"base_score": 1.5},
        {"base_score": -0.1},
        {"extra": 1},
        {"schema_version": 2},
        {"snippet": "x" * 301},
        {"excerpt": "x" * 2201},
        {"title": ""},
        {"canonical_url": "https://example.com/post#frag"},
        {"canonical_url": "https://Example.com/post"},
        {"canonical_url": "https://example.com/post?utm_source=x"},
        {"url": "ftp://example.com/x"},
        {"url": "https://user:pw@example.com/x"},
        {"published_at": "2026-10-15T05:12:00"},  # naive
        {"source_type": "scrape"},
        {"signals": {"stars": -1}},
    ],
)
def test_invalid(over: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        FeedItem.model_validate(feed_item(**over))


def test_item_id_helper() -> None:
    a = compute_item_id("https://example.com/post")
    assert len(a) == 16 and a == compute_item_id("https://example.com/post")
    assert a != compute_item_id("https://example.com/other")


def test_sixteen_slugs() -> None:
    assert len(TOPIC_SLUGS) == 16 and len(set(TOPIC_SLUGS)) == 16
