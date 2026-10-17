"""Rule-based tagging, no LLM (spec 5.2 step 5, A.4 matching rules)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from models.common import TopicSlug
from models.taxonomy import Taxonomy

MIN_BODY_HITS = (
    2  # assumption, tune on real fixtures: 1 hit in the title OR 2 distinct hits overall
)
MAX_TAGS = 6
MAX_ENTITIES = 20
_B_BEFORE, _B_AFTER = r"(?<![A-Za-z0-9])", r"(?![A-Za-z0-9])"


def _phrase(text: str) -> str:
    """Escaped regex where spaces and hyphens in the phrase are interchangeable."""
    return r"[\s-]+".join(re.escape(p) for p in re.split(r"[\s-]+", text.strip()) if p)


def _compile(text: str, *, case_sensitive: bool) -> re.Pattern[str]:
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(_B_BEFORE + _phrase(text) + _B_AFTER, flags)


@dataclass(frozen=True)
class TagResult:
    tags: list[TopicSlug]
    entities: list[str]
    entity_slugs: list[str]


@dataclass(frozen=True)
class _Span:
    start: int
    end: int
    slug: str


class Tagger:
    def __init__(self, taxonomy: Taxonomy) -> None:
        self._topics = [
            (
                slug,
                [
                    _compile(kw, case_sensitive=False)
                    for kw in dict.fromkeys([*t.keywords, *t.shared_keywords])
                ],
            )
            for slug, t in taxonomy.topics.items()
        ]
        self._order = {slug: i for i, (slug, _) in enumerate(self._topics)}
        # Names/aliases of 3 chars or fewer are case-sensitive ('xAI' must not match 'XAI').
        self._entities = [
            (
                slug,
                e.name,
                [_compile(a, case_sensitive=len(a) <= 3) for a in [e.name, *e.aliases]],
            )
            for slug, e in taxonomy.entities.items()
        ]

    def tag_topics(
        self, title: str, text: str, default_tags: Sequence[TopicSlug] | None = None
    ) -> list[TopicSlug]:
        scored: list[tuple[int, int, TopicSlug]] = []
        for slug, patterns in self._topics:
            title_hits = sum(1 for p in patterns if p.search(title))
            all_hits = sum(1 for p in patterns if p.search(title) or p.search(text))
            if title_hits >= 1 or all_hits >= MIN_BODY_HITS:
                scored.append((-(title_hits * 2 + all_hits), self._order[slug], slug))
        tags = [s for _, _, s in sorted(scored)]
        for d in default_tags or []:
            if d not in tags:
                tags.append(d)
        return tags[:MAX_TAGS]

    def tag_entities(self, title: str, text: str) -> list[str]:
        return [name for _, name in self.match_entities(title, text)]

    def match_entities(self, title: str, text: str) -> list[tuple[str, str]]:
        """Entities whose name or safe alias appears. Ambiguous aliases never qualify an entity on
        their own (they only matter once Phase 2 ranking sees the entity). Longest match wins."""
        haystack = f"{title}\n{text}"
        spans: list[_Span] = []
        for slug, _name, patterns in self._entities:
            for p in patterns:
                spans += [_Span(m.start(), m.end(), slug) for m in p.finditer(haystack)]
        keep: dict[str, int] = {}
        for s in spans:
            covered = any(
                o.slug != s.slug
                and o.start <= s.start
                and s.end <= o.end
                and (o.end - o.start) > (s.end - s.start)
                for o in spans
            )
            if not covered:
                keep[s.slug] = min(keep.get(s.slug, s.start), s.start)
        names = {slug: name for slug, name, _ in self._entities}
        ordered = sorted(keep.items(), key=lambda kv: kv[1])
        return [(s, names[s]) for s, _ in ordered][:MAX_ENTITIES]

    def tag(
        self, title: str, text: str, default_tags: Sequence[TopicSlug] | None = None
    ) -> TagResult:
        found = self.match_entities(title, text)
        return TagResult(
            self.tag_topics(title, text, default_tags),
            [n for _, n in found],
            [s for s, _ in found],
        )
