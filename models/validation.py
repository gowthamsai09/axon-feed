"""Semantic checks that report ALL problems instead of stopping at the first."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .authority import AuthorityConfig
from .sources import SourcesFile
from .taxonomy import Taxonomy


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def extend(self, other: Report) -> None:
        self.errors += other.errors
        self.warnings += other.warnings


def norm(text: str) -> str:
    return " ".join(text.casefold().split())


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.casefold())


def _contains_seq(hay: list[str], needle: list[str]) -> bool:
    n = len(needle)
    return n > 0 and any(hay[i : i + n] == needle for i in range(len(hay) - n + 1))


def check_taxonomy(tax: Taxonomy) -> Report:
    rep = Report()
    plain: dict[str, list[str]] = defaultdict(list)
    shared: dict[str, list[str]] = defaultdict(list)
    for slug, topic in tax.topics.items():
        seen: set[str] = set()
        for kw in [*topic.keywords, *topic.shared_keywords]:
            key = norm(kw)
            if key in seen:
                rep.errors.append(f"topic {slug}: keyword {kw!r} listed twice")
            seen.add(key)
        for kw in topic.keywords:
            plain[norm(kw)].append(slug)
        for kw in topic.shared_keywords:
            shared[norm(kw)].append(slug)
    for key, topics in plain.items():
        if len(set(topics)) > 1:
            rep.errors.append(
                f"keyword {key!r} appears in topics {sorted(set(topics))}; "
                "move it to shared_keywords in each, or remove duplicates"
            )
        if key in shared:
            rep.errors.append(
                f"keyword {key!r} is plain in {sorted(set(topics))} but shared in "
                f"{sorted(shared[key])}"
            )
    for key, topics in shared.items():
        if len(topics) < 2:
            rep.warnings.append(f"shared keyword {key!r} is only used in {topics}")

    all_kw = {k: sorted(set(plain.get(k, []) + shared.get(k, []))) for k in {*plain, *shared}}
    toks = {k: _tokens(k) for k in all_kw}
    for k, ts in sorted(toks.items()):
        if len(k) <= 3:
            rep.warnings.append(f"short keyword {k!r}: needs whole-token matching in Phase 1")
        for other, ots in sorted(toks.items()):
            if k != other and all_kw[k] != all_kw[other] and _contains_seq(ots, ts):
                rep.warnings.append(
                    f"keyword {k!r} {all_kw[k]} is contained in {other!r} {all_kw[other]}: "
                    "one item can trigger both topics"
                )

    owner: dict[str, str] = {}
    for eslug, ent in tax.entities.items():
        names = [ent.name, *ent.aliases, *ent.ambiguous_aliases]
        local: set[str] = set()
        for n in names:
            key = norm(n)
            if key in local:
                rep.errors.append(f"entity {eslug}: {n!r} repeated within the entity")
            local.add(key)
            if key in owner and owner[key] != eslug:
                rep.errors.append(f"{n!r} is used by entities {owner[key]} and {eslug}")
            owner.setdefault(key, eslug)
        for n in [*ent.aliases, *ent.ambiguous_aliases]:
            if len(n) <= 3:
                rep.warnings.append(f"entity {eslug}: short alias {n!r}")
    for key, owner_slug in sorted(owner.items()):
        if key in all_kw:
            rep.warnings.append(
                f"entity {owner_slug} alias {key!r} is also a topic keyword {all_kw[key]}"
                "  (ok if intended)"
            )
    return rep


def check_authority(cfg: AuthorityConfig) -> Report:
    rep = Report()
    tiers = sorted(cfg.tiers, key=lambda t: t.min_score)
    if tiers[0].min_score != 0:
        rep.errors.append("lowest tier must start at 0.00")
    if tiers[-1].max_score != 1:
        rep.errors.append("highest tier must end at 1.00")
    for a, b in zip(tiers, tiers[1:], strict=False):
        if round(a.max_score + 0.01, 2) != b.min_score:
            rep.errors.append(f"tiers {a.id} and {b.id} overlap or leave a gap")
    r = cfg.new_source_rule
    if r.default > r.cap_until_reviewed:
        rep.errors.append("new_source_rule.default exceeds cap_until_reviewed")
    return rep


def check_sources(
    src: SourcesFile, tax: Taxonomy | None = None, auth: AuthorityConfig | None = None
) -> Report:
    rep = Report()
    seen_urls: dict[str, str] = {}
    hosts: dict[str, set[float]] = defaultdict(set)
    for s in src.sources:
        if tax is not None:
            for tag in s.default_tags:
                if tag not in tax.topics:
                    rep.errors.append(f"source {s.id}: default tag {tag!r} not in taxonomy")
        if auth is not None and auth.tier_for(s.authority) is None:
            rep.errors.append(f"source {s.id}: authority {s.authority} is in no tier")
        for label, url in s.expand_urls():
            if url in seen_urls:
                rep.errors.append(f"{label}: duplicate URL also used by {seen_urls[url]}")
            seen_urls[url] = label
            hosts[urlsplit(url).netloc].add(s.rate_limit_per_min)
        if not s.verify:
            rep.warnings.append(f"source {s.id}: verify is false (not probed by validate_sources)")
    for host, limits in sorted(hosts.items()):
        if len(limits) > 1:
            rep.warnings.append(
                f"host {host} has differing rate limits {sorted(limits)}: "
                "define the per-domain rule"
            )
    for n in src.needs_research:
        rep.warnings.append(f"needs research: {n.id} ({n.name})")
    return rep
