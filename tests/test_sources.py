from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from models.sources import SourceEntry, SourcesFile
from models.validation import check_sources
from tests.factories import source


def gh(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "github-releases",
        "type": "github_releases",
        "repos": ["ollama/ollama", "vllm-project/vllm"],
        "url_template": "https://github.com/{repo}/releases.atom",
        "authority": 0.7,
        "default_tags": [],
        "rate_limit_per_min": 10,
    }
    base.update(over)
    return base


def test_valid_and_expand() -> None:
    assert SourceEntry.model_validate(source()).expand_urls()[0][1].startswith("https://")
    urls = SourceEntry.model_validate(gh()).expand_urls()
    assert urls[0] == (
        "github-releases[ollama/ollama]",
        "https://github.com/ollama/ollama/releases.atom",
    )


@pytest.mark.parametrize(
    "over",
    [
        {"authority": 1.2},
        {"authority": -0.1},
        {"rate_limit_per_min": 0},
        {"url": "http://insecure.example/feed"},
        {"url": None},
        {"id": "Bad_ID"},
        {"default_tags": ["nope"]},
        {"repos": ["a/b"]},  # repos only for github_releases
        {"extra": 1},
    ],
)
def test_invalid_rss(over: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        SourceEntry.model_validate(source(**over))


@pytest.mark.parametrize(
    "over",
    [
        {"repos": None},
        {"repos": []},
        {"repos": ["not-a-repo"]},
        {"repos": ["a/b", "a/b"]},
        {"url_template": "https://github.com/x/releases.atom"},
        {"url_template": "http://github.com/{repo}/releases.atom"},
        {"url": "https://github.com/a/b/releases.atom"},
    ],
)
def test_invalid_github(over: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        SourceEntry.model_validate(gh(**over))


def test_duplicate_ids_across_sections() -> None:
    nr = {"id": "hf-blog", "name": "X", "note": "n"}
    with pytest.raises(ValidationError):
        SourcesFile.model_validate({"sources": [source()], "needs_research": [nr]})
    with pytest.raises(ValidationError):
        SourcesFile.model_validate({"sources": [source(), source()]})


def test_check_sources_reports_duplicate_urls_and_rate_mismatch() -> None:
    a = source(id="a")
    b = source(id="b", rate_limit_per_min=3)
    rep = check_sources(SourcesFile.model_validate({"sources": [a, b]}))
    assert any("duplicate URL" in e for e in rep.errors)
    assert any("differing rate limits" in w for w in rep.warnings)
