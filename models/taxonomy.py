"""taxonomy.yaml (spec Appendix A.4)."""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import Field, model_validator

from .common import SLUG_PATTERN, TOPIC_SLUGS, StrictModel, TopicSlug

Keyword = Annotated[str, Field(min_length=1, max_length=64)]
EntitySlug = Annotated[str, Field(pattern=SLUG_PATTERN, max_length=64)]


class TopicDef(StrictModel):
    description: str = Field(min_length=1, max_length=200)
    keywords: list[Keyword] = Field(min_length=1)
    # Keywords deliberately shared with other topics (must be listed in every topic using them).
    shared_keywords: list[Keyword] = Field(default_factory=list)


class EntityDef(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    aliases: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(min_length=1)
    # Aliases that are common words or names. Matching rule (decided in Phase 1): only count
    # them when the name or a safe alias also appears in the same item.
    ambiguous_aliases: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(
        default_factory=list
    )
    notes: str | None = Field(None, max_length=300)


class Taxonomy(StrictModel):
    topics: dict[TopicSlug, TopicDef]
    entities: dict[EntitySlug, EntityDef]

    @model_validator(mode="after")
    def _exact_slugs(self) -> Self:
        missing = sorted(set(TOPIC_SLUGS) - set(self.topics))
        if missing:
            raise ValueError(f"topics missing required slugs: {missing}")
        return self
