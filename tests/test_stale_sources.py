"""Per-source staleness (spec 11.1, Appendix C 83): the pure rules, the store, and whole runs.
October 2026 calendar: the 12th is a Monday, so the 16th is a Friday and the 19th a Monday."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import pipeline.run as run_mod
from collectors.base import Collected, RawItem
from models.loaders import load_sources
from models.sources import SourceEntry
from pipeline.health import (
    DEFAULT_STALE_DAYS,
    Staleness,
    active_elapsed_days,
    evaluate,
    update_health,
)
from pipeline.store import SourceHealth, Store
from tests.factories import source

ROOT = Path(__file__).resolve().parent.parent
LONG = "A sufficiently long body of text about open models and local inference. " * 4


def utc(day: int, hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, second, tzinfo=UTC)


def test_the_calendar_premise_of_these_tests_holds() -> None:
    assert [utc(d).weekday() for d in (15, 16, 17, 18, 19, 22)] == [3, 4, 5, 6, 0, 3]


# ---- active_elapsed_days ------------------------------------------------------------------

IST = timezone(timedelta(hours=5, minutes=30))
SPANS = [
    ("plain three days", utc(19), utc(22), False, 3.0),
    ("plain half day", utc(19), utc(19, 12), False, 0.5),
    ("skip: Friday noon to Monday noon", utc(16, 12), utc(19, 12), True, 1.0),
    ("skip: a whole weekend", utc(17, 6), utc(18, 18), True, 0.0),
    ("skip: Thursday to the next Tuesday", utc(15), utc(20), True, 3.0),
    ("skip: Saturday noon to Monday noon", utc(17, 12), utc(19, 12), True, 0.5),
    ("skip: two weeks hold four weekend days", utc(5), utc(19), True, 10.0),
    ("now before since", utc(20), utc(19), True, 0.0),
    ("now equals since", utc(19), utc(19), False, 0.0),
    (
        "non-UTC input is converted first",
        datetime(2026, 10, 19, 5, 30, tzinfo=IST),
        utc(22),
        False,
        3.0,
    ),
]


@pytest.mark.parametrize(
    ("label", "since", "now", "skip", "expected"), SPANS, ids=[s[0] for s in SPANS]
)
def test_active_elapsed_days(
    label: str, since: datetime, now: datetime, skip: bool, expected: float
) -> None:
    assert active_elapsed_days(since, now, skip) == pytest.approx(expected)


# ---- update_health ------------------------------------------------------------------------

NOW = utc(20, 6)


def test_items_end_the_empty_period_and_record_the_time() -> None:
    previous = {"a": SourceHealth(last_items_at=utc(10), empty_since=utc(12))}
    assert update_health(previous, {"a": True}, NOW, {"a"})["a"] == SourceHealth(last_items_at=NOW)


def test_the_first_empty_run_starts_the_period_and_later_runs_keep_it() -> None:
    first = update_health({}, {"a": False}, utc(16, 12), {"a"})
    assert first["a"] == SourceHealth(last_items_at=None, empty_since=utc(16, 12))
    second = update_health(first, {"a": False}, utc(19, 12), {"a"})
    assert second["a"].empty_since == utc(16, 12)


def test_the_last_items_time_survives_an_empty_period() -> None:
    out = update_health({"a": SourceHealth(last_items_at=utc(10))}, {"a": False}, NOW, {"a"})
    assert out["a"] == SourceHealth(last_items_at=utc(10), empty_since=NOW)


def test_a_partial_run_keeps_the_others_and_unconfigured_sources_are_dropped() -> None:
    previous = {"a": SourceHealth(empty_since=utc(12)), "gone": SourceHealth(empty_since=utc(12))}
    out = update_health(previous, {"b": False}, NOW, known={"a", "b"})
    assert set(out) == {"a", "b"} and out["a"] == previous["a"] and out["b"].empty_since == NOW


def test_a_source_that_is_not_configured_is_never_added() -> None:
    assert update_health({}, {"x": True}, NOW, known={"a"}) == {}


def test_update_does_not_modify_its_input() -> None:
    previous = {"a": SourceHealth(empty_since=utc(12))}
    snapshot = dict(previous)
    update_health(previous, {"a": True}, NOW, {"a"})
    assert previous == snapshot


# ---- evaluate -----------------------------------------------------------------------------


def test_no_health_or_no_empty_period_is_never_stale() -> None:
    nothing = evaluate(None, NOW, threshold=None, skip_weekends=False)
    assert nothing == Staleness(0.0, DEFAULT_STALE_DAYS, False)
    recent = SourceHealth(last_items_at=utc(1))
    assert evaluate(recent, NOW, threshold=2, skip_weekends=True) == Staleness(0.0, 2, False)


def test_the_threshold_boundary_is_inclusive_and_one_second_short_is_not_stale() -> None:
    health = SourceHealth(empty_since=utc(19))
    exactly = evaluate(health, utc(22), threshold=None, skip_weekends=False)
    assert exactly == Staleness(3.0, 3, True)
    short = evaluate(health, utc(22) - timedelta(seconds=1), threshold=None, skip_weekends=False)
    assert (
        short.stale is False and short.empty_days == 3.0
    )  # the display rounds, the decision does not


def test_a_per_source_threshold_overrides_the_default() -> None:
    health = SourceHealth(empty_since=utc(19))
    now = utc(20, 1)
    assert evaluate(health, now, threshold=1, skip_weekends=False).stale is True
    assert evaluate(health, now, threshold=None, skip_weekends=False).stale is False
    assert evaluate(health, utc(30), threshold=30, skip_weekends=False).stale is False


def test_weekends_do_not_count_for_a_source_that_skips_them() -> None:
    health = SourceHealth(empty_since=utc(16, 12))
    now = utc(19, 12)
    assert evaluate(health, now, threshold=None, skip_weekends=False) == Staleness(3.0, 3, True)
    assert evaluate(health, now, threshold=None, skip_weekends=True) == Staleness(1.0, 3, False)


# ---- the models and the shipped config ----------------------------------------------------


def test_source_entry_defaults_and_valid_values() -> None:
    plain = SourceEntry.model_validate(source())
    assert plain.stale_after_days is None and plain.skip_weekends is False
    custom = SourceEntry.model_validate(source(stale_after_days=30, skip_weekends=True))
    assert custom.stale_after_days == 30 and custom.skip_weekends is True
    assert SourceEntry.model_validate(source(stale_after_days=1)).stale_after_days == 1


@pytest.mark.parametrize(
    "over",
    [
        {"stale_after_days": 0}, {"stale_after_days": 31}, {"stale_after_days": -1},
        {"stale_after_days": "soon"}, {"stale_after_days": 1.5}, {"skip_weekends": "maybe"},
    ],
)  # fmt: skip
def test_source_entry_rejects_bad_staleness_settings(over: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        SourceEntry.model_validate(source(**over))


def test_only_the_arxiv_sources_skip_weekends() -> None:
    sources = load_sources(ROOT / "config" / "sources.yaml").sources
    flagged = {s.id for s in sources if s.skip_weekends}
    assert flagged == {"arxiv-cs-ai", "arxiv-cs-cl", "arxiv-cs-lg"}


def test_an_old_store_without_health_still_loads_and_health_round_trips() -> None:
    fresh = Store(saved_at=NOW)
    data = json.loads(fresh.model_dump_json())
    del data["health"]
    assert Store.model_validate_json(json.dumps(data)).health == {}
    health = {"a": SourceHealth(last_items_at=utc(10), empty_since=utc(12))}
    full = Store(saved_at=NOW, health=health)
    assert Store.model_validate_json(full.model_dump_json()) == full
    with pytest.raises(ValidationError):
        SourceHealth.model_validate({"empty_since": None, "surprise": 1})


# ---- whole runs ---------------------------------------------------------------------------


@pytest.fixture
def plan(monkeypatch: pytest.MonkeyPatch) -> dict[str, Collected]:
    """What each source 'returns'; unlisted sources return nothing. No network is involved."""
    scripted: dict[str, Collected] = {}

    def fake_collect(sources: list[Any], _fetcher: Any, **_kw: Any) -> dict[str, Collected]:
        return {s.id: scripted.get(s.id, Collected()) for s in sources}

    monkeypatch.setattr(run_mod, "collect_all", fake_collect)
    return scripted


def with_one_good_source(plan: dict[str, Collected], now: datetime) -> None:
    """One source always has items, so the run itself succeeds (exit 0)."""
    post = RawItem(
        source_id="openai-news", source_type="rss", url="https://openai.com/a",
        title="A post about a new model release", published_at=now - timedelta(hours=1), text=LONG,
    )  # fmt: skip
    plan["openai-news"] = Collected(items=[post], fetched=1)


def run(out: Path, when: datetime, plan: dict[str, Collected], *extra: str) -> dict[str, Any]:
    with_one_good_source(plan, when)
    argv = ["--out", str(out), "--window-hours", "168", "--now", when.isoformat(), *extra]
    assert run_mod.main(argv) == 0
    meta: dict[str, Any] = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    return meta


def rows(meta: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r["id"]: r for r in meta["sources"]}


def test_weekends_matter_only_for_arxiv_and_recovery_clears_the_flag(
    tmp_path: Path, plan: dict[str, Collected]
) -> None:
    out = tmp_path / "feed"
    friday = rows(run(out, utc(16, 12), plan))
    assert friday["hn-ai"]["stale"] is False and friday["hn-ai"]["empty_days"] == 0.0
    assert friday["hn-ai"]["stale_after_days"] == 3

    monday = rows(run(out, utc(19, 12), plan))
    assert monday["hn-ai"]["stale"] is True and monday["hn-ai"]["empty_days"] == 3.0
    assert monday["arxiv-cs-ai"]["stale"] is False and monday["arxiv-cs-ai"]["empty_days"] == 1.0
    assert monday["openai-news"]["stale"] is False and monday["openai-news"]["empty_days"] == 0.0

    wednesday = rows(run(out, utc(21, 12), plan))
    assert wednesday["arxiv-cs-ai"]["stale"] is True
    assert wednesday["arxiv-cs-ai"]["empty_days"] == 3.0

    plan["hn-ai"] = Collected(fetched=5)  # the source returns entries again
    thursday = rows(run(out, utc(22, 12), plan))
    assert thursday["hn-ai"]["stale"] is False and thursday["hn-ai"]["empty_days"] == 0.0
    health = json.loads((out / "store.json").read_text(encoding="utf-8"))["health"]
    assert health["hn-ai"]["empty_since"] is None and health["hn-ai"]["last_items_at"] is not None
    assert health["arxiv-cs-ai"]["empty_since"] is not None


def test_a_failing_source_counts_as_empty(tmp_path: Path, plan: dict[str, Collected]) -> None:
    out = tmp_path / "feed"
    plan["hn-ai"] = Collected(failed=True, warnings=["hn-ai: HTTP 503"])
    run(out, utc(16, 12), plan)
    monday = rows(run(out, utc(19, 12), plan))["hn-ai"]
    assert monday["status"] == "failed" and monday["stale"] is True


def test_a_stateless_run_can_never_be_stale(tmp_path: Path, plan: dict[str, Collected]) -> None:
    out = tmp_path / "feed"
    run(out, utc(16, 12), plan, "--no-store")
    later = rows(run(out, utc(30, 12), plan, "--no-store"))
    assert later["hn-ai"]["stale"] is False and later["hn-ai"]["empty_days"] == 0.0
    assert not (out / "store.json").exists()


def test_a_partial_run_keeps_the_other_sources_health(
    tmp_path: Path, plan: dict[str, Collected]
) -> None:
    out = tmp_path / "feed"
    run(out, utc(16, 12), plan)
    meta = run(out, utc(19, 12), plan, "--only", "openai-news")
    assert [r["id"] for r in meta["sources"]] == ["openai-news"]
    health = json.loads((out / "store.json").read_text(encoding="utf-8"))["health"]
    assert health["hn-ai"]["empty_since"].startswith("2026-10-16T12:00:00")


def test_sources_removed_from_the_config_are_dropped_from_the_store(
    tmp_path: Path, plan: dict[str, Collected]
) -> None:
    out = tmp_path / "feed"
    run(out, utc(16, 12), plan)
    path = out / "store.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["health"]["ghost-source"] = {"last_items_at": None, "empty_since": "2026-10-01T00:00:00Z"}
    path.write_text(json.dumps(data), encoding="utf-8")
    run(out, utc(17, 12), plan)
    assert "ghost-source" not in json.loads(path.read_text(encoding="utf-8"))["health"]


def test_a_per_source_threshold_from_the_config_is_used(
    tmp_path: Path, plan: dict[str, Collected]
) -> None:
    config = tmp_path / "config"
    shutil.copytree(ROOT / "config", config)
    text = (config / "sources.yaml").read_text(encoding="utf-8")
    marker = "  - id: hn-ai\n"
    assert marker in text
    edited = text.replace(marker, marker + "    stale_after_days: 1\n", 1)
    (config / "sources.yaml").write_text(edited, encoding="utf-8")
    out = tmp_path / "feed"
    run(out, utc(16, 12), plan, "--config-dir", str(config))
    saturday = rows(run(out, utc(17, 12), plan, "--config-dir", str(config)))
    assert saturday["hn-ai"]["stale"] is True and saturday["hn-ai"]["stale_after_days"] == 1
    assert saturday["github-releases"]["stale"] is False
    assert saturday["github-releases"]["stale_after_days"] == 3
