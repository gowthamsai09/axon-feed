"""Dedupe and clustering (spec 5.2 step 4): exact by id, near-duplicate by title and simhash."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher


@dataclass
class Candidate:
    id: str
    canonical_url: str
    title: str
    source_id: str
    authority: float
    published_at: datetime
    excerpt: str = ""


@dataclass
class Cluster:
    primary: Candidate
    members: list[Candidate] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.members)

    def also_covered_by(self) -> list[tuple[str, str]]:
        return [(m.source_id, m.canonical_url) for m in self.members if m is not self.primary]


def normalize_title(title: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", title.casefold()))


def title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio()


def simhash(text: str) -> int:
    """64-bit simhash over 3-word shingles. Deterministic."""
    words = re.findall(r"[a-z0-9]+", text.casefold())
    shingles = [" ".join(words[i : i + 3]) for i in range(max(len(words) - 2, 1))] if words else []
    acc = [0] * 64
    for sh in shingles:
        h = int.from_bytes(hashlib.blake2b(sh.encode(), digest_size=8).digest(), "big")
        for bit in range(64):
            acc[bit] += 1 if (h >> bit) & 1 else -1
    return sum(1 << bit for bit in range(64) if acc[bit] > 0)


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def cluster(
    items: list[Candidate],
    *,
    title_threshold: float = 0.92,
    min_title_tokens: int = 4,
    simhash_distance: int = 3,
    min_excerpt_chars: int = 200,
) -> list[Cluster]:
    """Group duplicates. Near-duplicate rules apply only ACROSS different sources, and short
    titles (version strings like 'v0.5.1') never merge by title, so releases of different repos
    stay separate. Primary = highest authority, then earliest published, then id."""
    parent = list(range(len(items)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        parent[find(i)] = find(j)

    by_id: dict[str, int] = {}
    for i, it in enumerate(items):
        if it.id in by_id:
            union(i, by_id[it.id])
        else:
            by_id[it.id] = i
    norm = [normalize_title(it.title) for it in items]
    hashes = [simhash(it.excerpt) if len(it.excerpt) >= min_excerpt_chars else None for it in items]
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if items[i].source_id == items[j].source_id or find(i) == find(j):
                continue
            title_dup = (
                len(norm[i].split()) >= min_title_tokens
                and len(norm[j].split()) >= min_title_tokens
                and SequenceMatcher(None, norm[i], norm[j]).ratio() >= title_threshold
            )
            hi, hj = hashes[i], hashes[j]
            text_dup = hi is not None and hj is not None and hamming(hi, hj) <= simhash_distance
            if title_dup or text_dup:
                union(i, j)
    groups: dict[int, list[Candidate]] = defaultdict(list)
    for i, it in enumerate(items):
        groups[find(i)].append(it)
    out = []
    for members in groups.values():
        primary = min(members, key=lambda m: (-m.authority, m.published_at, m.id))
        out.append(Cluster(primary=primary, members=members))
    return sorted(out, key=lambda c: (c.primary.published_at, c.primary.id), reverse=True)
