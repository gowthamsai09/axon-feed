"""Minimal valid payloads for tests."""

from __future__ import annotations

from typing import Any


def feed_item(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": 1,
        "id": "9c1e4a7b2d3f5a60",
        "url": "https://example.com/post?utm_source=x",
        "canonical_url": "https://example.com/post",
        "title": "A title",
        "source_id": "example-blog",
        "source_type": "rss",
        "published_at": "2026-10-15T05:12:00Z",
        "fetched_at": "2026-10-15T06:07:11Z",
        "author": "someone",
        "snippet": "short text",
        "excerpt": "longer text",
        "tags": ["open-source-llms", "llm-releases"],
        "entities": ["Qwen"],
        "signals": {"stars": 0, "points": 0, "comments": 0},
        "cluster_id": "c-5b2e",
        "also_covered_by": [{"source_id": "hn", "url": "https://news.ycombinator.com/item?id=1"}],
        "base_score": 0.73,
    }
    base.update(over)
    return base


def summary(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "status": "ok",
        "title": "T",
        "one_line": "One line.",
        "key_points": ["a", "b"],
        "why_it_matters": "Because.",
        "tags": ["rag"],
        "entities": ["LangChain"],
        "notable_figures": [{"value": "7B", "context": "model size"}],
        "source_url": "https://example.com/post",
        "injection_flag": False,
    }
    base.update(over)
    return base


def source(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "hf-blog",
        "type": "rss",
        "url": "https://huggingface.co/blog/feed.xml",
        "authority": 0.8,
        "default_tags": ["open-source-llms"],
        "rate_limit_per_min": 6,
    }
    base.update(over)
    return base
