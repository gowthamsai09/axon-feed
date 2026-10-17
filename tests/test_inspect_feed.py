from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from pipeline import run as run_mod
from scripts.inspect_feed import main, summarize
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
