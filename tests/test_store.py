"""Rolling store (spec 5.6): merge, prune, persistence and the run-level retention behaviour."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

import pipeline.run as run_mod
from collectors.base import Collected, RawItem
from collectors.http import Fetcher, Throttle
from models.feed_item import Signals
from pipeline.extract import enrich, skip_extraction
from pipeline.store import (
    MAX_STORE_ITEMS,
    Store,
    StoredItem,
    build_store,
    key_of,
    load_store,
    merge_with_store,
    save_store,
)

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
LONG = "A sufficiently long body of text about open models and local inference. " * 4


TITLES = {
    1: "Falcon harbor lantern shipping schedule",
    2: "Quartz nebula orchid recipe collection",
    3: "Glacier velvet copper garden tour",
    4: "Summit anchor ribbon workshop dates",
}


def raw(
    n: int,
    *,
    source: str = "hf-blog",
    age_h: float | None = 1,
    text: str | None = None,
    points: int = 0,
    title: str | None = None,
) -> RawItem:
    return RawItem(
        source_id=source,
        source_type="rss",
        url=f"https://example.com/post-{n}",
        title=title or TITLES.get(n, f"Post number {n} about something different {n * 7}"),
        published_at=None if age_h is None else NOW - timedelta(hours=age_h),
        text=LONG.replace("open models", f"model number {n * 31}") if text is None else text,
        signals=Signals(points=points),
    )


def stored(r: RawItem, *, first: datetime = NOW, tried: int = 0) -> StoredItem:
    data = r.model_dump()
    data["published_at"] = r.published_at or first
    return StoredItem(**data, first_seen=first, extract_tried=tried)


# --- persistence ---------------------------------------------------------------------------


def test_missing_store_is_empty_without_warning(tmp_path: Path) -> None:
    store, warnings = load_store(tmp_path / "store.json", now=NOW)
    assert store.items == [] and store.redirects == {} and warnings == []


def test_save_and_load_round_trip_leaves_no_temp_file(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "store.json"
    original = Store(
        saved_at=NOW,
        items=[stored(raw(1), tried=1)],
        redirects={"https://bit.ly/a": "https://x.io/p"},
    )
    save_store(path, original)
    loaded, warnings = load_store(path, now=NOW)
    assert warnings == [] and loaded == original
    assert [p.name for p in path.parent.iterdir()] == ["store.json"]


@pytest.mark.parametrize(
    "content",
    ["{not json", '{"version": 2, "saved_at": "2026-10-16T12:00:00Z"}', '{"items": "nope"}', ""],
)
def test_unusable_store_is_ignored_with_a_warning(tmp_path: Path, content: str) -> None:
    path = tmp_path / "store.json"
    path.write_text(content, encoding="utf-8")
    store, warnings = load_store(path, now=NOW)
    assert store.items == [] and len(warnings) == 1 and "store.json" in warnings[0]


# --- merge ---------------------------------------------------------------------------------


def test_new_items_win_but_keep_longer_text_and_a_known_date() -> None:
    old = stored(raw(1, text=LONG * 3, points=1, age_h=5), first=NOW - timedelta(days=1))
    fresh = raw(1, text="short", points=9, age_h=None, title="Renamed post")
    m = merge_with_store([old], [fresh], now=NOW, window_hours=168)
    (item,) = m.raws
    assert item.signals.points == 9 and item.title == "Renamed post"  # fresh data wins
    assert item.text == LONG * 3  # the stored (extracted) text is longer
    assert item.published_at == old.published_at  # undated now, dated before
    assert m.first_seen[key_of(item)] == NOW - timedelta(days=1)
    assert m.retained == {}


def test_items_missing_from_this_run_are_retained_while_in_window() -> None:
    inside = stored(raw(1, age_h=100))
    outside = stored(raw(2, age_h=200))
    other_source = stored(raw(3, source="hn-ai", age_h=3))
    m = merge_with_store([inside, outside, other_source], [raw(4)], now=NOW, window_hours=168)
    assert [r.url for r in m.raws] == [
        "https://example.com/post-4",  # this run first
        "https://example.com/post-3",  # then retained, newest first
        "https://example.com/post-1",
    ]
    assert m.retained == {"hn-ai": 1, "hf-blog": 1}
    assert key_of(raw(4)) in m.first_seen and m.first_seen[key_of(raw(4))] == NOW


def test_a_shorter_window_prunes_the_store() -> None:
    old = stored(raw(1, age_h=100))
    assert merge_with_store([old], [], now=NOW, window_hours=72).raws == []


def test_build_store_pins_undated_items_counts_attempts_and_prunes_redirects() -> None:
    first = NOW - timedelta(hours=30)
    undated = raw(1, age_h=None)
    previous = stored(undated, first=first, tried=1)
    m = merge_with_store([previous], [undated], now=NOW, window_hours=168)
    kept_url, gone_url = undated.url, "https://example.com/gone"
    store, warnings = build_store(
        m.raws,
        m,
        now=NOW,
        redirects={kept_url: kept_url, gone_url: "https://elsewhere.io/x"},
        attempted={key_of(undated)},
    )
    (item,) = store.items
    assert item.published_at == first  # undated item keeps the time it was first seen
    assert item.first_seen == first and item.extract_tried == 2
    assert store.redirects == {kept_url: kept_url} and warnings == []


def test_store_is_capped_to_the_newest_items() -> None:
    raws = [raw(i, age_h=1 + i / 100) for i in range(MAX_STORE_ITEMS + 5)]
    m = merge_with_store([], raws, now=NOW, window_hours=168)
    store, warnings = build_store(m.raws, m, now=NOW, redirects={}, attempted=set())
    assert len(store.items) == MAX_STORE_ITEMS and len(warnings) == 1
    assert store.items[0].url == "https://example.com/post-0"


# --- extraction bookkeeping and the video/social skip --------------------------------------


def _fetcher(log: list[str]) -> Fetcher:
    def handler(req: httpx.Request) -> httpx.Response:
        log.append(str(req.url))
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200, text="<html><body><p>" + "Real article text. " * 30 + "</p></body></html>"
        )

    return Fetcher(
        httpx.Client(transport=httpx.MockTransport(handler)), Throttle(sleep=lambda _s: None)
    )


def test_enrich_skips_video_social_and_tried_items_and_records_attempts() -> None:
    thin = raw(1, text="")
    video = RawItem(
        source_id="hn-ai",
        source_type="api",
        url="https://www.youtube.com/watch?v=abc",
        title="Demo [video]",
        published_at=NOW,
        text="",
    )
    tried = raw(2, text="")
    log: list[str] = []
    attempted: set[tuple[str, str]] = set()
    out, warnings = enrich(
        [thin, video, tried],
        _fetcher(log),
        {},
        skip={key_of(tried)},
        attempted=attempted,
    )
    assert attempted == {key_of(thin)} and warnings == []
    assert len(out[0].text) > 100 and out[1].text == "" and out[2].text == ""
    assert not any("youtube" in u or "post-2" in u for u in log)


def test_skip_extraction_hosts() -> None:
    assert skip_extraction("https://www.youtube.com/watch?v=1") and skip_extraction(
        "https://x.com/a"
    )
    assert skip_extraction("https://mobile.twitter.com/a") and skip_extraction("https://youtu.be/z")
    assert not skip_extraction("https://netflix.com/a") and not skip_extraction(
        "https://example.com"
    )


# --- run level: retention across runs ------------------------------------------------------


@pytest.fixture
def scripted(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[RawItem]]:
    """collect_all returns whatever `plan` holds for each source, with no network at all."""
    plan: dict[str, list[RawItem]] = {}

    def fake_collect(sources: list[Any], _fetcher: Any, **_kw: Any) -> dict[str, Collected]:
        return {
            s.id: Collected(items=plan.get(s.id, []), fetched=len(plan.get(s.id, [])))
            for s in sources
        }

    monkeypatch.setattr(run_mod, "collect_all", fake_collect)
    return plan


def _run(out: Path, when: datetime, *extra: str) -> dict[str, Any]:
    code = run_mod.main(
        ["--out", str(out), "--window-hours", "168", "--now", when.isoformat(), *extra]
    )
    assert code in (0, 1)
    meta: dict[str, Any] = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    return meta


def _feed_urls(out: Path) -> set[str]:
    return {
        json.loads(f.read_text(encoding="utf-8"))["url"] for f in (out / "items").glob("*.json")
    }


def test_items_survive_runs_where_the_source_returns_nothing(
    tmp_path: Path, scripted: dict[str, list[RawItem]]
) -> None:
    out = tmp_path / "feed"
    scripted["hf-blog"] = [raw(1, age_h=60), raw(2, age_h=50)]
    _run(out, NOW)
    assert len(_feed_urls(out)) == 2

    scripted["hf-blog"] = []  # e.g. arXiv on a weekend: the source holds nothing today
    scripted["hn-ai"] = [raw(3, source="hn-ai", age_h=2)]
    meta = _run(out, NOW + timedelta(hours=3))
    assert _feed_urls(out) == {f"https://example.com/post-{n}" for n in (1, 2, 3)}
    rows = {r["id"]: r for r in meta["sources"]}
    assert rows["hf-blog"]["retained"] == 2 and rows["hf-blog"]["in_window"] == 2
    assert rows["hn-ai"]["retained"] == 0 and rows["hn-ai"]["in_window"] == 1
    assert meta["counts"]["items"] == 3
    store = json.loads((out / "store.json").read_text(encoding="utf-8"))
    assert len(store["items"]) == 3


def test_items_leave_when_they_age_out_of_the_window(
    tmp_path: Path, scripted: dict[str, list[RawItem]]
) -> None:
    out = tmp_path / "feed"
    scripted["hf-blog"] = [raw(1, age_h=60)]
    _run(out, NOW)
    scripted["hf-blog"] = []
    _run(out, NOW + timedelta(hours=100))  # item is now 160 h old: still inside 168 h
    assert len(_feed_urls(out)) == 1
    _run(out, NOW + timedelta(hours=120))  # 180 h old: out
    assert _feed_urls(out) == set()
    assert json.loads((out / "store.json").read_text(encoding="utf-8"))["items"] == []


def test_archive_does_not_duplicate_retained_items(
    tmp_path: Path, scripted: dict[str, list[RawItem]]
) -> None:
    out = tmp_path / "feed"
    scripted["hf-blog"] = [raw(1, age_h=5)]
    _run(out, NOW)
    scripted["hf-blog"] = []
    _run(out, NOW + timedelta(hours=3))
    _run(out, NOW + timedelta(hours=6))
    lines = [ln for p in (out / "archive").rglob("*.jsonl") for ln in p.read_text().splitlines()]
    assert len(lines) == 1


def test_no_store_runs_stateless(tmp_path: Path, scripted: dict[str, list[RawItem]]) -> None:
    out = tmp_path / "feed"
    scripted["hf-blog"] = [raw(1, age_h=5)]
    _run(out, NOW, "--no-store")
    assert not (out / "store.json").exists()
    scripted["hf-blog"] = []
    _run(out, NOW + timedelta(hours=3), "--no-store")
    assert _feed_urls(out) == set()


def test_corrupt_store_warns_and_the_run_continues(
    tmp_path: Path, scripted: dict[str, list[RawItem]]
) -> None:
    out = tmp_path / "feed"
    out.mkdir()
    (out / "meta.json").write_text("{}", encoding="utf-8")  # a previous run's output folder
    (out / "store.json").write_text("{broken", encoding="utf-8")
    scripted["hf-blog"] = [raw(1, age_h=5)]
    meta = _run(out, NOW)
    assert any(w.startswith("store: ignored") for w in meta["warnings"])
    assert len(_feed_urls(out)) == 1
    assert len(json.loads((out / "store.json").read_text(encoding="utf-8"))["items"]) == 1


def test_redirect_cache_is_persisted_and_not_fetched_again(
    tmp_path: Path, scripted: dict[str, list[RawItem]], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fake_resolve(self: Fetcher, url: str, *, per_min: float) -> str:
        calls.append(url)
        return "https://example.com/real-article"

    monkeypatch.setattr(Fetcher, "resolve", fake_resolve)
    out = tmp_path / "feed"
    short = RawItem(
        source_id="hn-ai",
        source_type="api",
        url="https://bit.ly/abc",
        title="Interesting read",
        published_at=NOW - timedelta(hours=2),
        text=LONG,
    )
    scripted["hn-ai"] = [short]
    _run(out, NOW)
    assert calls == ["https://bit.ly/abc"]
    store = json.loads((out / "store.json").read_text(encoding="utf-8"))
    assert store["redirects"] == {"https://bit.ly/abc": "https://example.com/real-article"}
    (item_file,) = (out / "items").glob("*.json")
    assert json.loads(item_file.read_text(encoding="utf-8"))["canonical_url"] == (
        "https://example.com/real-article"
    )

    _run(out, NOW + timedelta(hours=3))  # same short link again, now answered from the cache
    assert calls == ["https://bit.ly/abc"]
