"""Scoring (spec 5.3)."""

from __future__ import annotations

import math

W_RECENCY, W_AUTHORITY, W_ENGAGEMENT, W_CLUSTER = 0.40, 0.25, 0.20, 0.15
RECENCY_TAU_HOURS = 36.0
INTEREST_WEIGHT = 0.50
EXCLUSION_WEIGHT = 0.50


def recency(age_hours: float) -> float:
    return math.exp(-max(age_hours, 0.0) / RECENCY_TAU_HOURS)


def engagement(total: float, max_total: float) -> float:
    if max_total <= 0:
        return 0.0
    return min(math.log1p(max(total, 0.0)) / math.log1p(max_total), 1.0)


def cluster_size_norm(size: int, max_size: int) -> float:
    """Assumption (spec leaves it open): (size-1)/(max_size-1) over the window; 0 if no clusters."""
    if size < 1 or max_size <= 1:
        return 0.0
    return min((size - 1) / (max_size - 1), 1.0)


def base_score(
    *,
    age_hours: float,
    authority: float,
    engagement_total: float,
    max_engagement: float,
    cluster_size: int,
    max_cluster_size: int,
) -> float:
    score = (
        W_RECENCY * recency(age_hours)
        + W_AUTHORITY * min(max(authority, 0.0), 1.0)
        + W_ENGAGEMENT * engagement(engagement_total, max_engagement)
        + W_CLUSTER * cluster_size_norm(cluster_size, max_cluster_size)
    )
    return round(min(max(score, 0.0), 1.0), 4)


def user_score(base: float, interest_match: float, exclusion_penalty: float) -> float:
    """base + 0.50 * interest_match - 0.50 * exclusion_penalty (computed in Plane 2)."""
    return base + INTEREST_WEIGHT * interest_match - EXCLUSION_WEIGHT * exclusion_penalty
