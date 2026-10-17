from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from pipeline.dedupe import (
    Candidate,
    cluster,
    hamming,
    normalize_title,
    simhash,
    title_similarity,
)
from pipeline.score import base_score, cluster_size_norm, engagement, recency, user_score

T0 = datetime(2026, 10, 16, 6, 0, tzinfo=UTC)
LONG = "Model X is a new open weights language model trained on many tokens. " * 8


def cand(
    i: str, title: str, src: str, auth: float = 0.5, hours: int = 0, ex: str = ""
) -> Candidate:
    return Candidate(
        i, f"https://{src}.test/{i}", title, src, auth, T0 + timedelta(hours=hours), ex
    )


def test_recency_and_engagement_and_cluster_norm() -> None:
    assert recency(0) == 1 and recency(-5) == 1
    assert recency(36) == pytest.approx(math.exp(-1))
    assert engagement(0, 100) == 0 and engagement(100, 100) == 1 and engagement(5, 0) == 0
    assert 0 < engagement(10, 100) < 1
    assert cluster_size_norm(1, 1) == 0 and cluster_size_norm(3, 3) == 1
    assert cluster_size_norm(2, 3) == pytest.approx(0.5)


def test_base_score_bounds_and_ordering() -> None:
    kw = {"engagement_total": 10, "max_engagement": 100, "cluster_size": 1, "max_cluster_size": 3}
    fresh = base_score(age_hours=1, authority=0.9, **kw)
    old = base_score(age_hours=100, authority=0.9, **kw)
    low = base_score(age_hours=1, authority=0.3, **kw)
    assert 0 <= old < fresh <= 1 and low < fresh
    top = base_score(
        age_hours=0,
        authority=1,
        engagement_total=9,
        max_engagement=9,
        cluster_size=3,
        max_cluster_size=3,
    )
    assert top == 1.0
    assert user_score(0.5, 1.0, 0.5) == pytest.approx(0.75)


def test_title_and_simhash_helpers() -> None:
    assert normalize_title("Hello, WORLD!  v2") == "hello world v2"
    assert title_similarity("Qwen3 released today!", "qwen3 released today") == 1.0
    assert simhash(LONG) == simhash(LONG)
    assert hamming(simhash(LONG), simhash(LONG + " tiny change")) <= 3
    assert hamming(simhash(LONG), simhash("completely different words " * 40)) > 10


def test_exact_duplicates_merge_and_primary_is_highest_authority() -> None:
    items = [
        cand("a1", "Same", "s1", 0.5),
        cand("a1", "Same", "s2", 0.9),
        cand("b2", "Other", "s1"),
    ]
    clusters = cluster(items)
    assert sorted(c.size for c in clusters) == [1, 2]
    big = next(c for c in clusters if c.size == 2)
    assert big.primary.source_id == "s2" and len(big.also_covered_by()) == 1


def test_near_duplicate_titles_merge_across_sources_only() -> None:
    a = cand("a", "OpenAI releases a brand new open weights model today", "openai", 0.9)
    b = cand("b", "OpenAI releases a brand new open-weights model today!", "hn", 0.55)
    c = cand("c", "OpenAI releases a brand new open weights model today", "openai", 0.9)
    assert len(cluster([a, b])) == 1
    assert len(cluster([a, c])) == 2  # same source: never merged by title


def test_short_titles_never_merge() -> None:
    a = cand("a", "v0.5.1", "gh-ollama")
    b = cand("b", "v0.5.1", "gh-vllm")
    assert len(cluster([a, b])) == 2


def test_simhash_merge_needs_long_excerpts() -> None:
    a = cand("a", "Alpha story one", "s1", ex=LONG)
    b = cand("b", "Totally unrelated heading words", "s2", ex=LONG + " tiny change")
    assert len(cluster([a, b])) == 1
    c = cand("c", "Alpha story one", "s1", ex="short")
    d = cand("d", "Totally unrelated heading words", "s2", ex="short")
    assert len(cluster([c, d])) == 2


def test_cluster_order_is_newest_first_and_deterministic() -> None:
    items = [
        cand("old", "Old news item here today", "s1", hours=-5),
        cand("new", "New news item", "s2"),
    ]
    assert [c.primary.id for c in cluster(items)] == ["new", "old"]
    assert [c.primary.id for c in cluster(list(reversed(items)))] == ["new", "old"]
