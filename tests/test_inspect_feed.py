from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from pipeline import run as run_mod
from scripts.inspect_feed import drill_down, main, summarize
from tests.test_pipeline_run import router


@pytest.fixture
def feed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("collectors.http.time.sleep", lambda _s: None)
    monkeypatch.setattr(
        run_mod, "make_client", lambda *a, **k: httpx.Client(transport=httpx.MockTransport(router))
    )
    out = tmp_path / "feed"
    assert run_mod.main(["--out", str(out), "--now", "2026-10-16T12:00:00Z"]) == 0
    return out


def test_summary_sections(feed: Path) -> None:
    text = summarize(feed, top=5)
    for section in ("SOURCES", "WARNINGS", "TAGS", "ITEMS WITHOUT ANY TAG", "ENTITIES", "TOP 5"):
        assert section in text
    assert "arxiv-cs-ai" in text and "research-papers" in text


def test_cli(feed: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(feed)]) == 0 and "SOURCES" in capsys.readouterr().out
    assert main([str(tmp_path)]) == 2


def _synthetic_feed(root: Path) -> Path:
    """A tiny hand-written feed folder, independent of the fixtures."""
    (root / "items").mkdir(parents=True)
    meta = {
        "generated_at": "2026-10-17T00:00:00Z",
        "window_hours": 72,
        "counts": {"items": 3},
        "sources": [
            {"id": "hn-ai", "status": "ok", "fetched": 3, "collected": 3, "in_window": 3},
        ],
        "warnings": [],
    }
    (root / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    base = {"source_id": "hn-ai", "snippet": "line one\n\nline   two", "base_score": 0.5}
    rows = [
        {"id": "a1", "title": "Apple pie recipes", "tags": [], "entities": ["Apple"]},
        {"id": "a2", "title": "Agents everywhere", "tags": ["agents-tooling"], "entities": []},
        {"id": "a3", "title": "Nothing here", "tags": [], "entities": []},
    ]
    for row in rows:
        item = {**base, **row}
        (root / "items" / f"{row['id']}.json").write_text(json.dumps(item), encoding="utf-8")
    return root


def test_untagged_and_entity_examples(tmp_path: Path) -> None:
    text = summarize(_synthetic_feed(tmp_path / "f"), untagged=1, examples=1)
    assert "ITEMS WITHOUT ANY TAG: 2 of 3" in text
    assert "by source: hn-ai=2" in text
    assert "... 1 more (raise --untagged)" in text
    assert "line one line two" in text  # snippets are flattened to one line
    assert "- Apple pie recipes" in text  # example title under the entity


def test_drill_down_tag_and_entity(tmp_path: Path) -> None:
    feed = _synthetic_feed(tmp_path / "f")
    assert "TAG agents-tooling: 1 of 3" in drill_down(feed, tag="agents-tooling")
    by_entity = drill_down(feed, entity="apple", limit=5)
    assert "ENTITY apple: 1 of 3" in by_entity and "Apple pie recipes" in by_entity
    assert "more (raise --limit)" in drill_down(feed, tag="agents-tooling", limit=0)


def test_cli_drill_down(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    feed = _synthetic_feed(tmp_path / "f")
    assert main([str(feed), "--tag", "agents-tooling"]) == 0
    assert "TAG agents-tooling" in capsys.readouterr().out


def test_cli_save_writes_utf8(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    feed = _synthetic_feed(tmp_path / "f")
    target = tmp_path / "report.txt"
    assert main([str(feed), "--save", str(target)]) == 0
    assert "wrote" in capsys.readouterr().out
    assert "ITEMS WITHOUT ANY TAG: 2 of 3" in target.read_text(encoding="utf-8")
