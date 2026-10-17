"""Minimal, safe RSS 2.0 / Atom parser (defusedxml: no entity expansion)."""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from xml.etree.ElementTree import Element

from defusedxml import ElementTree as ET


@dataclass
class FeedEntry:
    title: str = ""
    link: str = ""
    date: datetime | None = None
    body: str = ""
    body_is_html: bool = True
    author: str | None = None
    categories: list[str] = field(default_factory=list)
    extra: dict[str, str] = field(default_factory=dict)  # any other child element, by local name


def _local(tag: object) -> str:
    return str(tag).rsplit("}", 1)[-1]


def parse_date(value: str | None) -> datetime | None:
    """RFC 822 or ISO 8601 -> aware datetime (naive input is taken as UTC). None if unparsable."""
    if not value or not value.strip():
        return None
    s = value.strip()
    try:
        dt = parsedate_to_datetime(s)
    except (TypeError, ValueError, IndexError):
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _text(el: Element) -> str:
    return "".join(el.itertext()).strip()


def _entry(el: Element, atom: bool) -> FeedEntry:
    e = FeedEntry()
    for child in el:
        name, txt = _local(child.tag), _text(child)
        if name == "title":
            e.title = " ".join(html.unescape(txt).split())
        elif name == "link":
            href = child.get("href")
            if atom and href:
                if child.get("rel", "alternate") == "alternate" and not e.link:
                    e.link = href.strip()
            elif not atom and txt and not e.link:
                e.link = txt
        elif name in {"pubDate", "published", "date"}:
            e.date = e.date or parse_date(txt)
        elif name == "updated":
            e.extra.setdefault("updated", txt)
        elif name in {"description", "summary", "content", "encoded"}:
            if txt and (name in {"content", "encoded"} or not e.body):
                e.body = txt
                e.body_is_html = child.get("type", "html") != "text"
        elif name == "author":
            nm = next((_text(c) for c in child if _local(c.tag) == "name"), txt)
            e.author = e.author or (nm or None)
        elif name == "creator":
            e.author = e.author or (txt or None)
        elif name == "category":
            term = child.get("term") or txt
            if term:
                e.categories.append(term)
        elif txt and name not in e.extra:
            e.extra[name] = txt
    if e.date is None:
        e.date = parse_date(e.extra.get("updated"))
    return e


def parse_feed(body: bytes) -> list[FeedEntry]:
    """Raises ValueError if the document is not parseable XML."""
    try:
        root = ET.fromstring(body)
    except Exception as exc:
        raise ValueError(f"invalid XML: {type(exc).__name__}") from exc
    atom = _local(root.tag) == "feed"
    kind = "entry" if atom else "item"
    return [_entry(el, atom) for el in root.iter() if _local(el.tag) == kind]
