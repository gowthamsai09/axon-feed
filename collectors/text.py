"""Plain-text cleaning for feed content. Output is safe to publish: no markup, no URLs
(GitHub release notes embed short-lived signed image URLs), no email addresses."""

from __future__ import annotations

import re
from html.parser import HTMLParser

_BLOCK = {"p", "br", "li", "div", "tr", "pre", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6"}
_SKIP = {"script", "style", "noscript", "template"}
_URL = re.compile(r"(?i)\b(?:[a-z][a-z0-9+.-]*://|www\.)\S+")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_NOISE = re.compile(r"(?i)^\s*(?:signed-off-by|co-authored-by)\s*:")


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP:
            self._skip += 1
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self._skip = max(self._skip - 1, 0)
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    p = _Text()
    p.feed(markup)
    p.close()
    return "".join(p.parts)


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[: max(limit - 1, 0)]
    space = cut.rfind(" ")
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:.-") + "\u2026"


def clean_text(raw: str, *, max_chars: int, is_html: bool = True) -> str:
    text = html_to_text(raw) if is_html else raw
    lines = [ln for ln in text.splitlines() if not _NOISE.match(ln)]
    text = _EMAIL.sub("", _URL.sub("", "\n".join(lines)))
    return truncate(" ".join(text.split()), max_chars)
