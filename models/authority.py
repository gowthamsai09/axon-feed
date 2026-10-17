"""authority.yaml: the authority scale (spec 4.1, 5.3)."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from .common import SourceId, StrictModel


class AuthorityTier(StrictModel):
    id: SourceId
    min_score: float = Field(ge=0, le=1)
    max_score: float = Field(ge=0, le=1)
    description: str = Field(min_length=1)
    examples: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _order(self) -> Self:
        if self.min_score > self.max_score:
            raise ValueError("min_score must be <= max_score")
        return self


class NewSourceRule(StrictModel):
    default: float = Field(ge=0, le=1)
    cap_until_reviewed: float = Field(ge=0, le=1)
    steps: list[str] = Field(min_length=1)


class AuthorityConfig(StrictModel):
    version: Literal[1]
    tiers: list[AuthorityTier] = Field(min_length=2)
    new_source_rule: NewSourceRule

    def tier_for(self, score: float) -> str | None:
        for t in self.tiers:
            if t.min_score <= score <= t.max_score:
                return t.id
        return None
