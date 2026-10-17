"""Collection window (spec 5.6). Development default 24 h; production 168 h (7 days)."""

from __future__ import annotations

from datetime import datetime, timedelta

DEV_WINDOW_HOURS = 24
PROD_WINDOW_HOURS = 168


def window_start(now: datetime, hours: float) -> datetime:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if hours <= 0:
        raise ValueError("window hours must be positive")
    return now - timedelta(hours=hours)


def effective_date(published_at: datetime | None, fetched_at: datetime) -> tuple[datetime, bool]:
    """(date to use, True if the item had no date and fetch time was substituted)."""
    if published_at is None:
        return fetched_at, True
    if published_at.tzinfo is None:
        raise ValueError("published_at must be timezone-aware")
    return published_at, False


def in_window(published_at: datetime | None, *, now: datetime, hours: float) -> bool:
    """True if published within the last `hours`. Undated items count as published now."""
    if published_at is None:
        return True
    return published_at >= window_start(now, hours)
