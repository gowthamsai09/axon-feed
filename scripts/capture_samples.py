"""Capture trimmed REAL responses as test fixtures. Run on YOUR machine (needs network):

    python -m scripts.capture_samples            # writes tests/fixtures/*, keeps 5 entries each

Collectors are built and tested against these files, not against guesses. Reuses the polite
client rules of validate_sources (robots.txt, User-Agent, per-host throttle). No secrets involved.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from collectors.http import MAX_BYTES, PLACEHOLDER_CONTACT, Robots, Throttle, user_agent
from models.loaders import ConfigError, load_sources
from scripts.validate_sources import CONFIG

FIXTURES = Path("tests") / "fixtures"
_BLOCK = re.compile(r"(?s)<(item|entry)[\s>].*?</\1>")


def safe_name(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "__", label.replace(".", "_")).strip("_")


_SECRET_PARAM = re.compile(
    r"(?i)((?:jwt|token|signature|x-amz-signature|x-amz-credential|sig)=)[^&\s\"'<>]+"
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_CONTENT = re.compile(r"(?s)(<content\b[^>]*>)(.*?)(</content>)")


def shrink_content(text: str, max_chars: int) -> str:
    """Cut oversized Atom <content> bodies (release notes can be 100s of KB) at a whitespace."""

    def cut(m: re.Match[str]) -> str:
        body = m.group(2)
        if len(body) <= max_chars:
            return m.group(0)
        body = body[:max_chars].rsplit(" ", 1)[0]
        return m.group(1) + re.sub(r"&[#\w]*$", "", body) + m.group(3)

    return _CONTENT.sub(cut, text)


def sanitize(text: str, max_content_chars: int = 6000) -> str:
    """LF line endings; redact signed-URL tokens (GitHub release notes embed short-lived JWTs)
    and email addresses; shrink huge <content> bodies. Fixtures are committed to git."""
    text = text.replace("\r\n", "\n")
    text = _SECRET_PARAM.sub(r"\1REDACTED", text)
    text = _EMAIL.sub("redacted@example.invalid", text)
    return shrink_content(text, max_content_chars)


def trim_xml(text: str, keep: int) -> str:
    """Keep the first `keep` <item>/<entry> blocks; everything else (header/footer) is untouched."""
    blocks = list(_BLOCK.finditer(text))
    if len(blocks) <= keep:
        return text
    return text[: blocks[keep - 1].end()] + text[blocks[-1].end() :]


def trim_json(data: Any, keep: int) -> Any:
    if isinstance(data, list):
        return data[:keep]
    if isinstance(data, dict):
        return {k: (v[:keep] if isinstance(v, list) else v) for k, v in data.items()}
    return data


def trim(kind: str, body: bytes, keep: int) -> tuple[str, str]:
    """(extension, trimmed text)."""
    if kind == "api":
        text = json.dumps(trim_json(json.loads(body), keep), indent=2, ensure_ascii=False)
        return ".json", sanitize(text)
    return ".xml", sanitize(trim_xml(body.decode("utf-8", errors="replace"), keep))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path, default=CONFIG)
    ap.add_argument("--items", type=int, default=5)
    ap.add_argument("--out", type=Path, default=FIXTURES)
    ap.add_argument("--only")
    ap.add_argument("--contact", default=os.environ.get("AXON_CONTACT_URL", PLACEHOLDER_CONTACT))
    args = ap.parse_args(argv)
    try:
        cfg = load_sources(args.config)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []
    with httpx.Client(
        headers={"User-Agent": user_agent(args.contact)}, timeout=20.0, follow_redirects=True
    ) as client:
        robots, throttle = Robots(client), Throttle()
        for entry in cfg.sources:
            if args.only not in (None, entry.id):
                continue
            for label, url in entry.expand_urls():
                allowed, note = robots.check(url)
                rec: dict[str, Any] = {"source": label, "url": url}
                if not allowed:
                    rec["status"] = f"skipped: {note or 'robots'}"
                    manifest.append(rec)
                    print(f"  skipped {label}", file=sys.stderr)
                    continue
                throttle.wait(re.sub(r"^https?://([^/]+).*", r"\1", url), entry.rate_limit_per_min)
                try:
                    resp = client.get(url, headers={"Accept": "*/*"})
                    body = resp.content[:MAX_BYTES]
                    ext, text = trim(entry.type, body, args.items)
                    name = safe_name(label) + ext
                    (args.out / name).write_text(text, encoding="utf-8")
                    rec.update(
                        status=resp.status_code,
                        file=name,
                        content_type=resp.headers.get("content-type", ""),
                        final_url=str(resp.url),
                        etag=bool(resp.headers.get("etag")),
                    )
                    print(f"  saved   {name}", file=sys.stderr)
                except (httpx.HTTPError, ValueError) as exc:
                    rec["status"] = f"failed: {type(exc).__name__}"
                    print(f"  FAILED  {label}: {exc}", file=sys.stderr)
                manifest.append(rec)
    meta = {
        "captured_at": datetime.now(UTC).isoformat(),
        "items_kept": args.items,
        "files": manifest,
    }
    (args.out / "_manifest.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"wrote {len(manifest)} entries to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
