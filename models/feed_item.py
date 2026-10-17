"""Feed item (spec Appendix A.1)."""

from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import parse_qsl, urlsplit

from pydantic import AwareDatetime, Field, field_validator

from .common import (
    ITEM_ID_PATTERN,
    HttpUrlStr,
    SourceId,
    SourceType,
    StrictModel,
    TopicSlug,
    is_tracking_param,
)


class Signals(StrictModel):
    stars: int = Field(0, ge=0)
    points: int = Field(0, ge=0)
    comments: int = Field(0, ge=0)


class AlsoCoveredBy(StrictModel):
    source_id: SourceId
    url: HttpUrlStr


class FeedItem(StrictModel):
    schema_version: Literal[1]
    id: str = Field(pattern=ITEM_ID_PATTERN)
    url: HttpUrlStr
    canonical_url: HttpUrlStr
    title: str = Field(min_length=1, max_length=500)
    source_id: SourceId
    source_type: SourceType
    published_at: AwareDatetime
    fetched_at: AwareDatetime
    author: str | None = Field(None, max_length=200)
    snippet: str = Field(max_length=300)  # spec: "first ~280 chars"
    excerpt: str | None = Field(None, max_length=2200)  # spec: "up to ~2000 chars"
    tags: list[TopicSlug] = Field(default_factory=list, max_length=16)
    entities: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(
        default_factory=list, max_length=20
    )
    signals: Signals = Field(default_factory=Signals)
    cluster_id: str | None = Field(None, max_length=64)
    also_covered_by: list[AlsoCoveredBy] = Field(default_factory=list, max_length=50)
    base_score: float = Field(ge=0, le=1)  # each term in spec 5.3 is in [0,1], weights sum to 1

    @field_validator("canonical_url")
    @classmethod
    def _canonical_rules(cls, v: str) -> str:
        parts = urlsplit(v)
        if parts.fragment:
            raise ValueError("canonical_url must not contain a fragment")
        if parts.netloc != parts.netloc.lower():
            raise ValueError("canonical_url host must be lowercase")
        for key, _ in parse_qsl(parts.query, keep_blank_values=True):
            if is_tracking_param(key):
                raise ValueError(f"canonical_url must not contain tracking parameter {key!r}")
        return v

    @field_validator("tags", "entities")
    @classmethod
    def _unique(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("duplicate values")
        return v
