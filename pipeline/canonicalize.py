"""URL canonicalization (spec 5.2). Pure: redirect resolution is a separate network step."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from models.common import compute_item_id, is_tracking_param

_ARXIV_HOSTS = {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}
# Assumption (verify with captured arXiv fixtures, Appendix B #14): abs/pdf/html paths, optional vN.
_ARXIV_PATH = re.compile(
    r"^/(?:abs|pdf|html)/(?P<id>\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?(?:\.pdf)?$"
)


def canonicalize_url(url: str) -> str:
    """https, lowercase host, no default port, no fragment, no tracking params, sorted query,
    no trailing slash (except root). arXiv abs/pdf/html and version suffixes collapse to /abs/<id>.
    Raises ValueError for non-http(s), hostless or credentialed URLs."""
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise ValueError(f"not an http(s) URL: {url!r}")
    if parts.username or parts.password:
        raise ValueError("credentials in URL")
    host = parts.hostname.rstrip(".").lower()
    port = parts.port
    netloc = f"[{host}]" if ":" in host else host
    if port not in (None, 80, 443):
        netloc += f":{port}"
    path = re.sub(r"/{2,}", "/", parts.path) or "/"
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    query_pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    query = urlencode(sorted(kv for kv in query_pairs if not is_tracking_param(kv[0])))
    if host in _ARXIV_HOSTS and (m := _ARXIV_PATH.match(path)):
        netloc, path, query = "arxiv.org", f"/abs/{m['id']}", ""
    return urlunsplit(("https", netloc, path, query, ""))


def item_id_for(url: str) -> tuple[str, str]:
    """(id, canonical_url) for a raw URL."""
    canon = canonicalize_url(url)
    return compute_item_id(canon), canon
