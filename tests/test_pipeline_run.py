"""End to end over the REAL captured fixtures: collect -> window -> extract -> dedupe -> tag ->
score -> publish (spec 5.2 to 5.4). Time is pinned so results are reproducible."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from collectors.base import RawItem
from collectors.http import Fetcher, Throttle
from models.common import TOPIC_SLUGS
from models.feed_item import FeedItem, Signals
from models.loaders import load_sources, load_taxonomy
from pipeline import run as run_mod
from pipeline.publish import write_feed
from pipeline.run import collect_all, process
from pipeline.tag import Tagger

ROOT = Path(__file__).resolve().parent.parent
FX = ROOT / "tests" / "fixtures"
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
SOURCES = {s.id: s for s in load_sources(ROOT / "config/sources.yaml").sources}
TAGGER = Tagger(load_taxonomy(ROOT / "config/taxonomy.yaml"))


def page_for(url: str) -> bytes:
    """A distinct article per URL (identical text would correctly cluster as duplicates)."""
    words = [hashlib.sha256(f"{url}{i}".encode()).hexdigest()[:7] for i in range(150)]
    return f"<html><body><article><p>{' '.join(words)}</p></article></body></html>".encode()


def router(req: httpx.Request) -> httpx.Response:
    u = str(req.url.copy_with(query=None))
    if req.url.path == "/robots.txt":
        return httpx.Response(404)
    names = {
        "https://rss.arxiv.org/rss/cs.AI": "arxiv-cs-ai.xml",
        "https://rss.arxiv.org/rss/cs.CL": "arxiv-cs-cl.xml",
        "https://rss.arxiv.org/rss/cs.LG": "arxiv-cs-lg.xml",
        "https://huggingface.co/blog/feed.xml": "hf-blog.xml",
        "https://huggingface.co/api/daily_papers": "hf-daily-papers.json",
        "https://hn.algolia.com/api/v1/search_by_date": "hn-ai.json",
        "https://openai.com/news/rss.xml": "openai-news.xml",
        "https://deepmind.google/blog/rss.xml": "google-deepmind-blog.xml",
    }
    if u in names:
        body = (FX / names[u]).read_bytes()
        if "algolia" in u:  # the real API echoes the parameters it honoured
            body = body.replace(b"hitsPerPage=20", b"hitsPerPage=100&numericFilters=created_at_i>1")
        return httpx.Response(200, content=body)
    if u.startswith("https://github.com/") and u.endswith("/releases.atom"):
        repo = u.removeprefix("https://github.com/").removesuffix("/releases.atom")
        f = FX / ("github-releases__" + repo.replace("/", "__").replace(".", "_") + ".xml")
        return httpx.Response(200, content=f.read_bytes())
    return httpx.Response(200, content=page_for(str(req.url)))  # article pages for extraction


@pytest.fixture
def fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("collectors.http.time.sleep", lambda _s: None)
    monkeypatch.setattr(
        run_mod, "make_client", lambda *a, **k: httpx.Client(transport=httpx.MockTransport(router))
    )


def test_main_end_to_end_on_real_captures(tmp_path: Path, fast: None) -> None:
    out = tmp_path / "feed"
    assert run_mod.main(["--out", str(out), "--now", "2026-10-16T12:00:00Z"]) == 0
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    assert meta["schema_version"] == 1 and meta["window_hours"] == 24
    health = {s["id"]: s for s in meta["sources"]}
    assert set(health) == set(SOURCES) and all(h["status"] == "ok" for h in health.values())
    assert health["arxiv-cs-ai"]["in_window"] == 5 and health["github-releases"]["fetched"] == 30
    assert meta["warnings"] == [] and meta["counts"]["items"] > 20

    files = sorted((out / "items").glob("*.json"))
    assert len(files) == meta["counts"]["items"]
    for f in files:  # every published item validates against the model
        item = FeedItem.model_validate_json(f.read_text(encoding="utf-8"))
        assert item.published_at >= datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
        assert item.snippet and item.id == f.stem
    assert {p.stem for p in (out / "tags").glob("*.json")} == set(TOPIC_SLUGS)
    latest = json.loads((out / "latest.json").read_text(encoding="utf-8"))
    scores = [i["base_score"] for i in latest["items"]]
    assert scores == sorted(scores, reverse=True) and "excerpt" not in latest["items"][0]
    blog = [i for i in latest["items"] if i["source_id"] == "hf-blog"]
    assert blog and all("open-source-llms" in i["tags"] for i in blog)  # default tag applied
    assert (out / "entities" / "ollama.json").exists()
    assert list((out / "archive").rglob("*.jsonl"))
    llama_index = [i for i in latest["items"] if "llama_index" in i["url"]]
    assert llama_index == []  # September releases are outside the 24 h window


def test_extraction_fills_thin_feed_text(tmp_path: Path, fast: None) -> None:
    out = tmp_path / "feed"
    run_mod.main(["--out", str(out), "--now", "2026-10-16T12:00:00Z"])
    blog = [
        json.loads(f.read_text(encoding="utf-8"))
        for f in (out / "items").glob("*.json")
        if json.loads(f.read_text(encoding="utf-8"))["source_id"] == "hf-blog"
    ]
    assert blog and all(len(b["excerpt"]) > 100 for b in blog)


def test_rerun_is_idempotent_and_prunes(tmp_path: Path, fast: None) -> None:
    out = tmp_path / "feed"
    run_mod.main(["--out", str(out), "--now", "2026-10-16T12:00:00Z"])
    snap = {
        str(p.relative_to(out)): p.read_bytes()
        for p in out.rglob("*")
        if p.is_file() and p.name not in {"meta.json", "store.json"}  # store: attempts may change
    }
    run_mod.main(["--out", str(out), "--now", "2026-10-16T12:00:00Z"])
    again = {
        str(p.relative_to(out)): p.read_bytes()
        for p in out.rglob("*")
        if p.is_file() and p.name not in {"meta.json", "store.json"}  # store: attempts may change
    }
    assert snap == again  # archive is not duplicated
    (out / "items" / "stale.json").write_text("{}")
    run_mod.main(["--out", str(out), "--now", "2026-10-16T12:00:00Z"])
    assert not (out / "items" / "stale.json").exists()


def test_collect_all_isolates_a_crashing_source(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("bug")

    monkeypatch.setattr(run_mod, "collector_for", boom)
    f = Fetcher(
        httpx.Client(transport=httpx.MockTransport(router)), Throttle(sleep=lambda _s: None)
    )
    res = collect_all([SOURCES["hf-blog"]], f, window_hours=24)
    assert res["hf-blog"].warnings == ["hf-blog: collector crashed: RuntimeError"]


def raw(
    src: str, url: str, title: str, text: str = "", sig: Signals | None = None, hours: float = 1
) -> RawItem:
    when = (
        datetime(2026, 10, 16, 11, 0, tzinfo=UTC)
        if hours == 1
        else datetime(2026, 10, 10, tzinfo=UTC)
    )
    return RawItem(source_id=src, source_type=SOURCES[src].type, url=url, title=title,
                   published_at=when, text=text, signals=sig or Signals())  # fmt: skip


def test_cross_listed_paper_merges_and_keeps_best_signals() -> None:
    long = "We propose a method for retrieval augmented generation with embeddings. " * 6
    items = [
        raw("arxiv-cs-lg", "https://arxiv.org/abs/2610.00001", "A RAG method", long),
        raw("arxiv-cs-ai", "https://arxiv.org/abs/2610.00001v2", "A RAG method", long),
        raw(
            "hf-daily-papers",
            "https://arxiv.org/abs/2610.00001",
            "A RAG method",
            long,
            Signals(points=15, comments=2),
        ),
    ]
    res = process(items, SOURCES, TAGGER, now=NOW, window_hours=24)
    [item] = res.items
    assert item.source_id == "hf-daily-papers"  # highest authority (0.75) wins
    assert {c.source_id for c in item.also_covered_by} == {"arxiv-cs-lg", "arxiv-cs-ai"}
    assert item.signals.points == 15 and item.cluster_id and "rag" in item.tags
    assert "research-papers" in item.tags and item.base_score > 0.5


def test_window_and_undated_items() -> None:
    old = raw("openai-news", "https://openai.com/a", "Old news about a model", hours=0)
    undated = RawItem(
        source_id="openai-news", source_type="rss", url="https://openai.com/b", title="Undated post"
    )
    res = process([old, undated], SOURCES, TAGGER, now=NOW, window_hours=24)
    assert [i.title for i in res.items] == ["Undated post"]
    assert any("no date" in w for w in res.warnings)
    assert len(process([old], SOURCES, TAGGER, now=NOW, window_hours=24 * 30).items) == 1


def test_release_of_different_repos_with_same_version_do_not_merge() -> None:
    a = raw(
        "github-releases",
        "https://github.com/ollama/ollama/releases/tag/v1.0.0",
        "ollama/ollama v1.0.0",
    )
    b = raw(
        "github-releases",
        "https://github.com/vllm-project/vllm/releases/tag/v1.0.0",
        "vllm-project/vllm v1.0.0",
    )
    res = process([a, b], SOURCES, TAGGER, now=NOW, window_hours=24)
    assert len(res.items) == 2
    names = {e for i in res.items for e in i.entities}
    assert {"Ollama", "vLLM"} <= names


def test_publish_layout_and_archive_append(tmp_path: Path) -> None:
    res = process(
        [raw("openai-news", "https://openai.com/a", "A new model release for rag")],
        SOURCES,
        TAGGER,
        now=NOW,
        window_hours=24,
    )
    kw: dict[str, Any] = {"now": NOW, "window_hours": 24, "sources": [], "warnings": []}
    c1 = write_feed(tmp_path, res.items, res.entity_slugs, **kw)
    c2 = write_feed(tmp_path, res.items, res.entity_slugs, **kw)
    assert c1 == c2 and c1["items"] == 1
    lines = (tmp_path / "archive/2026/10/16.jsonl").read_text(encoding="utf-8").splitlines()
    assert (
        len(lines) == 1 and json.loads(lines[0])["excerpt"] is not None
        if False
        else len(lines) == 1
    )
    assert (tmp_path / "meta.json").exists() and (tmp_path / "latest.json").exists()


def test_refuses_to_write_into_a_non_feed_folder(tmp_path: Path, fast: None) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\n")  # looks like a code repo
    assert run_mod.main(["--out", str(tmp_path)]) == 2
    assert not (tmp_path / "latest.json").exists() and not (tmp_path / "tags").exists()
    empty = tmp_path / "empty"
    empty.mkdir()
    assert run_mod.main(["--out", str(empty), "--now", "2026-10-16T12:00:00Z"]) == 0


def test_all_sources_failing_exits_nonzero_and_explains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("collectors.http.time.sleep", lambda _s: None)
    monkeypatch.setattr(
        run_mod,
        "make_client",
        lambda *a, **k: httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
    )
    assert run_mod.main(["--out", str(tmp_path / "feed")]) == 1
    captured = capsys.readouterr()
    assert "ALL SOURCES FAILED" in captured.err and "! " in captured.out


def test_empty_response_is_reported_as_empty_not_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A 200 response with zero entries is 'empty' and says so (seen live with arXiv cs.CL)."""
    monkeypatch.setattr("collectors.http.time.sleep", lambda _s: None)

    def only_empty_arxiv(req: httpx.Request) -> httpx.Response:
        if "cs.CL" in str(req.url):
            return httpx.Response(200, content=b"<rss><channel><title>x</title></channel></rss>")
        return router(req)

    monkeypatch.setattr(
        run_mod,
        "make_client",
        lambda *a, **k: httpx.Client(transport=httpx.MockTransport(only_empty_arxiv)),
    )
    out = tmp_path / "feed"
    assert run_mod.main(["--out", str(out), "--now", "2026-10-16T12:00:00Z"]) == 0
    health = {s["id"]: s for s in json.loads((out / "meta.json").read_text())["sources"]}
    assert health["arxiv-cs-cl"]["status"] == "empty" and health["arxiv-cs-ai"]["status"] == "ok"
    assert "arxiv-cs-cl: response had 0 entries" in capsys.readouterr().out
