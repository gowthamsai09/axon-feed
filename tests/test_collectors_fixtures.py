"""Collectors against REAL captured responses (tests/fixtures, captured 2026-10-16)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from collectors.arxiv import parse_arxiv
from collectors.github_releases import (
    GithubReleasesCollector,
    is_prerelease,
    parse_releases,
    tag_from_link,
)
from collectors.hf import parse_daily_papers
from collectors.hn import HackerNewsCollector, parse_hits
from collectors.http import Fetcher, Throttle
from collectors.registry import collector_for
from collectors.rss import parse_rss
from models.loaders import load_sources
from models.sources import SourceEntry

FX = Path(__file__).resolve().parent / "fixtures"
SRC = {
    s.id: s
    for s in load_sources(Path(__file__).resolve().parent.parent / "config/sources.yaml").sources
}


def fx(name: str) -> bytes:
    return (FX / name).read_bytes()


@pytest.mark.parametrize("sid", ["arxiv-cs-ai", "arxiv-cs-cl", "arxiv-cs-lg"])
def test_arxiv(sid: str) -> None:
    c = parse_arxiv(SRC[sid], fx(f"{sid}.xml"))
    assert c.fetched == 5 and len(c.items) == 5 and not c.warnings
    for i in c.items:
        assert re.fullmatch(r"https://arxiv\.org/abs/\d{4}\.\d{5}", i.url)
        assert "Announce Type" not in i.text and not i.text.startswith("arXiv:")
        assert i.published_at == datetime(2026, 10, 16, 4, 0, tzinfo=UTC)  # 00:00 -0400
        assert i.author and len(i.text) > 200


def test_arxiv_skips_replacements() -> None:
    xml = (
        b'<rss xmlns:arxiv="http://arxiv.org/schemas/atom"><channel>'
        b"<item><title>Old paper</title><link>https://arxiv.org/abs/2501.00001</link>"
        b"<description>arXiv:2501.00001v3 Announce Type: replace \nAbstract: revised</description>"
        b"<arxiv:announce_type>replace</arxiv:announce_type></item>"
        b"<item><title>New paper</title><link>https://arxiv.org/abs/2610.00002</link>"
        b"<description>arXiv:2610.00002v1 Announce Type: cross \n"
        b"Abstract: fresh abstract</description>"
        b"<arxiv:announce_type>cross</arxiv:announce_type></item></channel></rss>"
    )
    c = parse_arxiv(SRC["arxiv-cs-ai"], xml)
    assert c.fetched == 2 and [i.title for i in c.items] == ["New paper"]
    assert c.items[0].text == "fresh abstract"


def test_rss_feeds() -> None:
    hf = parse_rss(SRC["hf-blog"], fx("hf-blog.xml"))
    assert len(hf.items) == 5 and all(i.text == "" for i in hf.items)  # needs page extraction
    assert all(i.url.startswith("https://huggingface.co/blog/") for i in hf.items)
    oa = parse_rss(SRC["openai-news"], fx("openai-news.xml"))
    assert len(oa.items) == 5 and all(i.text for i in oa.items)
    assert oa.items[0].published_at == datetime(2026, 10, 15, 19, 0, tzinfo=UTC)
    dm = parse_rss(SRC["google-deepmind-blog"], fx("google-deepmind-blog.xml"))
    assert len(dm.items) == 5 and not dm.warnings


def test_release_tag_helpers() -> None:
    assert (
        tag_from_link("https://github.com/o/r/releases/tag/langchain-core%3D%3D1.6.11")
        == "langchain-core==1.6.11"
    )
    for pre in (
        "v0.40.5-rc2",
        "v0.32.0rc2",
        "v1.0.0-beta.1",
        "v2.0.0-alpha",
        "v1.2.3-dev4",
        "nightly",
    ):
        assert is_prerelease(pre), pre
    for ok in ("v0.40.5", "b11685", "proto-v0.6.0", "langchain-core==1.6.11", "Release v5.20.0"):
        assert not is_prerelease(ok), ok


@pytest.mark.parametrize(
    ("repo", "kept"),
    [("ollama/ollama", 2), ("vllm-project/vllm", 2), ("ggml-org/llama.cpp", 5),
     ("langchain-ai/langchain", 5), ("run-llama/llama_index", 5), ("huggingface/transformers", 5)],
)  # fmt: skip
def test_github_releases(repo: str, kept: int) -> None:
    name = "github-releases__" + repo.replace("/", "__").replace(".", "_") + ".xml"
    c = parse_releases(SRC["github-releases"], repo, fx(name))
    assert c.fetched == 5 and len(c.items) == kept and not c.warnings
    for i in c.items:
        assert i.url.startswith(f"https://github.com/{repo}/releases/tag/")
        assert repo.split("/")[1].lower() in i.title.lower()
        assert i.published_at is not None
        low = i.text.lower()
        assert (
            "jwt=" not in low and "://" not in low and "@corp" not in low
        )  # no URLs/tokens/emails
        assert "&lt;" not in i.text and "&amp;" not in i.text  # entities were decoded


def test_github_titles() -> None:
    c = parse_releases(
        SRC["github-releases"], "ollama/ollama", fx("github-releases__ollama__ollama.xml")
    )
    assert c.items[0].title == "ollama/ollama v0.40.5"
    c = parse_releases(
        SRC["github-releases"], "ggml-org/llama.cpp", fx("github-releases__ggml-org__llama_cpp.xml")
    )
    assert c.items[1].title == "ggml-org/llama.cpp b11685"
    c = parse_releases(
        SRC["github-releases"],
        "langchain-ai/langchain",
        fx("github-releases__langchain-ai__langchain.xml"),
    )
    assert c.items[0].title == "langchain-core==1.6.11"  # already names the project: no prefix


def test_hf_daily_papers() -> None:
    c = parse_daily_papers(SRC["hf-daily-papers"], fx("hf-daily-papers.json"))
    assert len(c.items) == 5 and not c.warnings
    first = c.items[0]
    assert first.url == "https://arxiv.org/abs/2610.22352" and first.signals.points == 1
    assert first.published_at == datetime(2026, 10, 16, tzinfo=UTC)
    assert c.items[1].signals.points == 10 and c.items[1].signals.comments == 2
    assert all(i.author and i.text for i in c.items)


def test_hf_rejects_non_list_and_bad_entries() -> None:
    with pytest.raises(ValueError):
        parse_daily_papers(SRC["hf-daily-papers"], b'{"a": 1}')
    c = parse_daily_papers(SRC["hf-daily-papers"], b'[{"paper": {"id": "bad"}, "title": "t"}, 5]')
    assert not c.items and len(c.warnings) == 2


def test_hn_gate_and_urls() -> None:
    c, n, echoed = parse_hits(SRC["hn-ai"], fx("hn-ai.json"))
    assert n == 5 and "hitsPerPage=20" in echoed
    assert [i.title for i in c.items] == [
        "A 4B LLM learns to write tools that other LLMs can use",
        "AI as a part of capitalism \u2013 the right way to go",
        "Sift \u2013 Analyse every file with local LLMs",
    ]  # 'limit'/'limited' fuzzy matches are dropped
    assert c.items[0].url == "https://tool-use-smith.github.io/"
    assert c.items[1].url == "https://news.ycombinator.com/item?id=50115335"  # Ask HN: no url
    assert "<p>" not in c.items[1].text and "&#x27;" not in c.items[1].text
    assert c.items[1].signals.points == 2


def _hn_fetcher(echo: str, seen: list[str]) -> Fetcher:
    page = fx("hn-ai.json").decode().replace("hitsPerPage=20", echo)

    def h(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        return httpx.Response(404 if req.url.path == "/robots.txt" else 200, text=page)

    return Fetcher(httpx.Client(transport=httpx.MockTransport(h)), Throttle(sleep=lambda _s: None))


def test_hn_fetch_sends_window_params_and_warns_when_ignored() -> None:
    now = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
    col = HackerNewsCollector(SRC["hn-ai"], window_hours=24, now=now)
    seen: list[str] = []
    ok = col.fetch(_hn_fetcher("hitsPerPage=100&numericFilters=created_at_i>1", seen))
    assert len(ok.items) == 3 and not ok.warnings
    since = int(datetime(2026, 10, 15, 12, 0, tzinfo=UTC).timestamp())
    api = [u for u in seen if "robots" not in u]
    assert len(api) == 1 and f"created_at_i%3E{since}" in api[0] and "hitsPerPage=100" in api[0]
    bad = col.fetch(_hn_fetcher("hitsPerPage=20", []))
    assert any("ignored query parameters" in w for w in bad.warnings)


def test_github_collector_survives_one_failing_repo() -> None:
    body = fx("github-releases__ollama__ollama.xml")

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        return (
            httpx.Response(200, content=body) if "ollama" in req.url.path else httpx.Response(500)
        )

    f = Fetcher(httpx.Client(transport=httpx.MockTransport(h)), Throttle(sleep=lambda _s: None))
    c = GithubReleasesCollector(SRC["github-releases"]).fetch(f)
    assert len(c.items) == 2 and len(c.warnings) == 5 and all("HTTP 500" in w for w in c.warnings)


def test_registry_maps_every_configured_source() -> None:
    kinds = {sid: type(collector_for(s, window_hours=24)).__name__ for sid, s in SRC.items()}
    assert kinds["arxiv-cs-ai"] == "ArxivCollector" and kinds["hf-blog"] == "RssCollector"
    assert (
        kinds["hn-ai"] == "HackerNewsCollector"
        and kinds["github-releases"] == "GithubReleasesCollector"
    )
    assert kinds["hf-daily-papers"] == "HfDailyPapersCollector"
    odd = SourceEntry.model_validate(
        {"id": "x-api", "type": "api", "url": "https://x.test/a", "authority": 0.5,
         "default_tags": [], "rate_limit_per_min": 1}
    )  # fmt: skip
    with pytest.raises(ValueError):
        collector_for(odd, window_hours=24)


def test_hn_gate_needs_more_than_one_incidental_mention() -> None:
    from collectors.hn import on_topic

    assert on_topic("Show HN: a local LLM runner", "")  # title
    assert on_topic("Faster inference tricks", "We tested LLMs. Also more LLM data.")  # 2 in text
    assert on_topic("What is hard as AI makes coding easier?", "Since LLMs came, ...")  # AI title
    assert not on_topic("Ask HN: Dark mode for HN please?", "As an LLM aside, light mode hurts.")
    assert not on_topic("Reversing Wing Commander", "Not LLM generated, just patient work.")
    assert not on_topic("A plea to help me help my father", "My LLM-free life ...")
    assert not on_topic("Limits of limited things", "limit limited")
