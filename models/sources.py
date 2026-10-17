"""sources.yaml (spec Appendix A.3, extended with verify/repos/url_template/needs_research)."""

from __future__ import annotations

import re
from typing import Self

from pydantic import Field, model_validator

from .common import HttpsUrlStr, SourceId, SourceType, StrictModel, TopicSlug

REPO_PATTERN = r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"


class SourceEntry(StrictModel):
    id: SourceId
    type: SourceType
    url: HttpsUrlStr | None = None
    authority: float = Field(ge=0, le=1)
    default_tags: list[TopicSlug] = Field(max_length=16)
    rate_limit_per_min: float = Field(gt=0, le=600)
    # Extensions beyond A.3 (all optional):
    verify: bool = False
    repos: list[str] | None = None
    url_template: str | None = Field(None, max_length=2048)
    note: str | None = Field(None, max_length=500)

    @model_validator(mode="after")
    def _type_rules(self) -> Self:
        if len(set(self.default_tags)) != len(self.default_tags):
            raise ValueError("duplicate default_tags")
        if self.type == "github_releases":
            if self.url is not None:
                raise ValueError("github_releases uses repos + url_template, not url")
            if not self.repos:
                raise ValueError("github_releases requires a non-empty repos list")
            if len(set(self.repos)) != len(self.repos):
                raise ValueError("duplicate repos")
            for repo in self.repos:
                if not re.fullmatch(REPO_PATTERN, repo):
                    raise ValueError(f"bad repo {repo!r}, expected owner/name")
            tpl = self.url_template or ""
            if "{repo}" not in tpl or not tpl.startswith("https://"):
                raise ValueError("url_template must be https and contain '{repo}'")
        else:
            if self.url is None:
                raise ValueError(f"type {self.type!r} requires url")
            if self.repos is not None or self.url_template is not None:
                raise ValueError("repos/url_template are only valid for github_releases")
        return self

    def expand_urls(self) -> list[tuple[str, str]]:
        """(label, url) pairs to fetch for this source."""
        if self.type == "github_releases":
            tpl = self.url_template or ""
            return [(f"{self.id}[{r}]", tpl.format(repo=r)) for r in self.repos or []]
        return [(self.id, self.url or "")]


class NeedsResearchEntry(StrictModel):
    """A source whose feed (if any) is unknown. Never put guessed feed URLs here."""

    id: SourceId
    name: str = Field(min_length=1, max_length=100)
    homepage: HttpsUrlStr | None = None  # starting point for research, NOT a feed
    note: str = Field(min_length=1, max_length=500)


class SourcesFile(StrictModel):
    sources: list[SourceEntry] = Field(min_length=1)
    needs_research: list[NeedsResearchEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        ids = [s.id for s in self.sources] + [n.id for n in self.needs_research]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate source ids: {dupes}")
        return self
