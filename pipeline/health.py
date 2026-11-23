"""Per-source staleness (spec 11.1, Appendix C 83). Pure functions: the clock is always passed in.

A source "has items" in a run when it returned at least one entry and did not fail. The first run
without items starts the empty period; any run with items ends it. A source is stale once the active
time since the empty period began reaches its threshold (default 3 days). With `skip_weekends`
(arXiv, which announces Monday to Friday) Saturday and Sunday, in UTC, do not count."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

from pipeline.store import SourceHealth

DEFAULT_STALE_DAYS = 3
SECONDS_PER_DAY = 86400.0


def active_elapsed_days(since: datetime, now: datetime, skip_weekends: bool) -> float:
    """Days from `since` to `now`; Saturday and Sunday (UTC) are left out when `skip_weekends`."""
    since, now = since.astimezone(UTC), now.astimezone(UTC)
    if now <= since:
        return 0.0
    if not skip_weekends:
        return (now - since).total_seconds() / SECONDS_PER_DAY
    total = 0.0
    cursor = since
    while cursor < now:
        midnight = datetime.combine(cursor.date() + timedelta(days=1), time(0), tzinfo=UTC)
        end = min(midnight, now)
        if cursor.weekday() < 5:  # Monday to Friday
            total += (end - cursor).total_seconds()
        cursor = end
    return total / SECONDS_PER_DAY


def update_health(
    previous: Mapping[str, SourceHealth],
    got_items: Mapping[str, bool],
    now: datetime,
    known: Collection[str],
) -> dict[str, SourceHealth]:
    """New health map. Sources not in `got_items` (a partial run) keep their old state; sources no
    longer configured (not in `known`) are dropped."""
    out = {sid: health for sid, health in previous.items() if sid in known}
    for sid, ok in got_items.items():
        if sid not in known:
            continue
        before = previous.get(sid, SourceHealth())
        if ok:
            out[sid] = SourceHealth(last_items_at=now, empty_since=None)
        else:
            out[sid] = SourceHealth(
                last_items_at=before.last_items_at, empty_since=before.empty_since or now
            )
    return out


@dataclass(frozen=True)
class Staleness:
    empty_days: float  # rounded to 2 decimals, for display
    threshold: int
    stale: bool


def evaluate(
    health: SourceHealth | None, now: datetime, *, threshold: int | None, skip_weekends: bool
) -> Staleness:
    limit = threshold if threshold is not None else DEFAULT_STALE_DAYS
    if health is None or health.empty_since is None:
        return Staleness(0.0, limit, False)
    days = active_elapsed_days(health.empty_since, now, skip_weekends)
    return Staleness(round(days, 2), limit, days >= limit)
