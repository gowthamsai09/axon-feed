"""Summarizer output (spec Appendix A.2 and the rules in section 13.1)."""

from __future__ import annotations

import re
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from .common import Depth, HttpUrlStr, StrictModel, TopicSlug

SummaryStatus = Literal["ok", "insufficient_content", "suspected_injection"]

ONE_LINE_MAX = 200
WHY_MAX = 300
MAX_TAGS = 4
MAX_ENTITIES = 8
DEPTH_LIMITS: dict[str, tuple[int, int]] = {  # depth -> (max key points, max chars each)
    "headlines": (0, 0),
    "brief": (3, 160),
    "detailed": (6, 240),
}

_URL_RE = re.compile(r"(?i)(?:\b[a-z][a-z0-9+.-]*://|\bwww\.)\S+")
_HTML_RE = re.compile(r"</?[A-Za-z][^>]*>")


class NotableFigure(StrictModel):
    value: str = Field(min_length=1, max_length=200)
    context: str = Field(min_length=1, max_length=200)


class Summary(StrictModel):
    status: SummaryStatus
    title: str = Field(max_length=300)
    one_line: str = Field(max_length=ONE_LINE_MAX)
    key_points: list[Annotated[str, Field(max_length=240)]] = Field(max_length=6)
    why_it_matters: str = Field(max_length=WHY_MAX)
    tags: list[TopicSlug] = Field(max_length=MAX_TAGS)
    entities: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(
        max_length=MAX_ENTITIES
    )
    notable_figures: list[NotableFigure] = Field(max_length=10)
    source_url: HttpUrlStr
    injection_flag: bool

    def _strings(self) -> list[str]:
        out = [self.title, self.one_line, self.why_it_matters]
        out += self.key_points + list(self.tags) + self.entities
        for fig in self.notable_figures:
            out += [fig.value, fig.context]
        return out

    @model_validator(mode="after")
    def _rules(self) -> Self:
        # Rule 10: plain text only, no URLs other than source_url, no markdown/HTML/fences.
        for s in self._strings():
            if _URL_RE.search(s):
                raise ValueError("URLs are not allowed in content fields")
            if "```" in s or _HTML_RE.search(s) or "](" in s:
                raise ValueError("markdown, HTML and code fences are not allowed")
        if len(set(self.tags)) != len(self.tags):
            raise ValueError("duplicate tags")
        if self.status == "ok":
            if not self.title.strip() or not self.one_line.strip():
                raise ValueError("status 'ok' requires title and one_line")
        else:
            content = [
                self.title,
                self.one_line,
                self.key_points,
                self.why_it_matters,
                self.tags,
                self.entities,
                self.notable_figures,
            ]
            if any(content):
                raise ValueError("content fields must be empty unless status is 'ok'")
            if self.status == "suspected_injection" and not self.injection_flag:
                raise ValueError("suspected_injection requires injection_flag=true")
        return self


def validate_for_depth(summary: Summary, depth: Depth) -> list[str]:
    """Depth-specific caps from rule 12. Returns a list of problems (empty = fine)."""
    max_points, max_chars = DEPTH_LIMITS[depth]
    problems: list[str] = []
    if len(summary.key_points) > max_points:
        problems.append(f"depth '{depth}' allows at most {max_points} key points")
    problems += [
        f"key point {i} exceeds {max_chars} chars"
        for i, kp in enumerate(summary.key_points)
        if len(kp) > max_chars
    ]
    return problems


def verify_against_source(
    summary: Summary,
    *,
    source_url: str,
    source_text: str,
    allowed_tags: list[str] | None = None,
) -> list[str]:
    """Code-side checks from section 13.1 that need the source. Empty list = accept."""
    problems: list[str] = []
    if summary.source_url != source_url:
        problems.append("source_url does not match the task's source_url")
    problems += [
        f"notable figure {fig.value!r} not found in source text"
        for fig in summary.notable_figures
        if fig.value not in source_text
    ]
    if allowed_tags is not None:
        extra = sorted(set(summary.tags) - set(allowed_tags))
        if extra:
            problems.append(f"tags outside allowed_tags: {extra}")
    return problems
