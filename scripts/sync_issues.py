"""Open or close one GitHub Issue per stale source (spec 11.1, Appendix C 83).

  python -m scripts.sync_issues --meta data/feed/meta.json [--dry-run]

Reads the per-source rows the collect run wrote to meta.json (`stale`, `empty_days`, ...). A stale
source gets ONE Issue titled `Stale source: <id>` with the label `source-stale`; the Issue is closed
again when the source recovers. Idempotent: an open Issue is never duplicated, and a manually closed
one is not reopened for 3 days. Runs in its own workflow step so the token never shares a process with
the scraper. A GitHub failure is a warning, never a failed run: the exit code is 0 unless the input is
unusable (2). Messages carry HTTP status codes only: never the token, a response body or feed text."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

API = "https://api.github.com"
LABEL = "source-stale"
TITLE_PREFIX = "Stale source: "
MAX_CREATE = 10  # per run
COOLDOWN = timedelta(days=3)  # after a manual close, do not open the same Issue again
SOURCE_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")  # always used with fullmatch
REPO = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")
SERVER = re.compile(r"https://[A-Za-z0-9.-]{1,100}(?::\d{1,5})?")
DIGITS = re.compile(r"\d{1,20}")


class GithubError(Exception):
    """A GitHub call failed. The message is the status code only."""

    def __init__(self, status: int) -> None:
        text = "GitHub API unreachable" if status == 0 else f"GitHub API returned HTTP {status}"
        super().__init__(text)
        self.status = status


@dataclass(frozen=True)
class Row:
    id: str
    status: str
    empty_days: float
    threshold: int
    stale: bool


@dataclass(frozen=True)
class Existing:
    number: int
    source: str
    open: bool
    closed_at: datetime | None


@dataclass
class Report:
    created: list[str] = field(default_factory=list)
    closed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def parse_rows(meta: object) -> list[Row]:
    """Tolerant reader of meta.json: anything unexpected is skipped, nothing is trusted as text."""
    sources = meta.get("sources") if isinstance(meta, dict) else None
    if not isinstance(sources, list):
        return []
    rows: list[Row] = []
    for raw in sources:
        if not isinstance(raw, dict):
            continue
        sid = raw.get("id")
        if not isinstance(sid, str) or len(sid) > 64 or SOURCE_ID.fullmatch(sid) is None:
            continue
        status = raw.get("status")
        days = raw.get("empty_days")
        limit = raw.get("stale_after_days")
        good_days = isinstance(days, int | float) and not isinstance(days, bool)
        good_limit = isinstance(limit, int) and not isinstance(limit, bool) and 1 <= limit <= 30
        rows.append(
            Row(
                id=sid,
                status=status if status in {"ok", "empty", "failed"} else "unknown",
                empty_days=float(days) if good_days else 0.0,
                threshold=limit if good_limit else 3,
                stale=raw.get("stale") is True,
            )
        )
    return rows


def _when(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def existing(raw: object) -> list[Existing]:
    """Our Issues among the labelled ones. Pull requests and foreign titles are ignored."""
    if not isinstance(raw, list):
        raise GithubError(502)
    found: list[Existing] = []
    for item in raw:
        if not isinstance(item, dict) or "pull_request" in item:
            continue
        title, number = item.get("title"), item.get("number")
        if not isinstance(title, str) or not title.startswith(TITLE_PREFIX):
            continue
        sid = title[len(TITLE_PREFIX) :]
        if SOURCE_ID.fullmatch(sid) is None or isinstance(number, bool) or not isinstance(number, int):
            continue
        found.append(Existing(number, sid, item.get("state") == "open", _when(item.get("closed_at"))))
    return found


class Issues:
    def __init__(self, client: httpx.Client, repo: str, token: str) -> None:
        if REPO.fullmatch(repo) is None:
            raise ValueError("repository must look like owner/name")
        self._client, self._repo = client, repo
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "axon-feed-stale-sources",
        }

    def _call(
        self, method: str, path: str, *, params: dict[str, str | int] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:  # fmt: skip
        try:  # redirects are not followed (httpx default): the token never travels elsewhere
            resp = self._client.request(
                method, f"{API}/repos/{self._repo}{path}", params=params, json=body,
                headers=self._headers,
            )  # fmt: skip
        except httpx.HTTPError:
            raise GithubError(0) from None
        if resp.status_code >= 300:
            raise GithubError(resp.status_code)
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            raise GithubError(502) from None

    def list(self) -> list[Existing]:
        params: dict[str, str | int] = {"labels": LABEL, "state": "all", "per_page": 100}
        return existing(self._call("GET", "/issues", params=params))

    def create(self, row: Row, run_url: str) -> None:
        text = (
            f"The source `{row.id}` has returned no items for {row.empty_days:.1f} active days "
            f"(threshold: {row.threshold}). Last status: {row.status}.\n\n"
            "Check `meta.json` on the data branch and the source's feed. "
            "This Issue closes by itself when the source returns items again."
        )
        if run_url:
            text += f"\n\nCollect run: {run_url}"
        body = {"title": f"{TITLE_PREFIX}{row.id}", "body": text, "labels": [LABEL]}
        self._call("POST", "/issues", body=body)

    def close(self, number: int) -> None:
        note = {"body": "The source returned items again. Closing."}
        self._call("POST", f"/issues/{number}/comments", body=note)
        done = {"state": "closed", "state_reason": "completed"}
        self._call("PATCH", f"/issues/{number}", body=done)


def sync(rows: list[Row], issues: Issues, now: datetime, run_url: str = "") -> Report:
    report = Report()
    try:
        found = issues.list()
    except GithubError as exc:
        report.warnings.append(f"issues: {exc}")
        return report
    open_by: dict[str, list[Existing]] = {}
    for e in found:
        if e.open:
            open_by.setdefault(e.source, []).append(e)
    cooling = {
        e.source for e in found if not e.open and e.closed_at and now - e.closed_at < COOLDOWN
    }
    try:
        for row in rows:
            if row.stale:
                if row.id in open_by or row.id in cooling:
                    continue
                if len(report.created) >= MAX_CREATE:
                    report.warnings.append("issues: creation cap reached for this run")
                    break
                issues.create(row, run_url)
                report.created.append(row.id)
            elif row.id in open_by:
                for e in open_by[row.id]:
                    issues.close(e.number)
                report.closed.append(row.id)
    except GithubError as exc:  # one failure ends the run of calls (a 403 will not fix itself)
        report.warnings.append(f"issues: {exc}")
    return report


def run_url_from(env: Mapping[str, str]) -> str:
    server = env.get("GITHUB_SERVER_URL", "").rstrip("/")
    repo, run_id = env.get("GITHUB_REPOSITORY", ""), env.get("GITHUB_RUN_ID", "")
    if SERVER.fullmatch(server) and REPO.fullmatch(repo) and DIGITS.fullmatch(run_id):
        return f"{server}/{repo}/actions/runs/{run_id}"
    return ""


def main(
    argv: list[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    client: httpx.Client | None = None,
    now: datetime | None = None,
) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--meta", type=Path, default=Path("data/feed/meta.json"))
    ap.add_argument("--dry-run", action="store_true", help="print what would be done, call nothing")
    args = ap.parse_args(argv)
    environment = os.environ if env is None else env
    try:
        rows = parse_rows(json.loads(args.meta.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        print("cannot read the feed meta file", file=sys.stderr)
        return 2
    if args.dry_run:
        stale = [r.id for r in rows if r.stale]
        print(f"dry run: {len(stale)} stale source(s): {', '.join(stale) or 'none'}")
        return 0
    repo = environment.get("GITHUB_REPOSITORY", "")
    token = environment.get("GH_TOKEN") or environment.get("GITHUB_TOKEN") or ""
    if REPO.fullmatch(repo) is None:
        print("GITHUB_REPOSITORY is not set to owner/name", file=sys.stderr)
        return 2
    if not token:
        print("GH_TOKEN (or GITHUB_TOKEN) is not set", file=sys.stderr)
        return 2
    http = client or httpx.Client(timeout=20.0)
    when = now or datetime.now(UTC)
    try:
        report = sync(rows, Issues(http, repo, token), when, run_url_from(environment))
    finally:
        if client is None:
            http.close()
    for warning in report.warnings:
        print(f"::warning::{warning.replace(token, '***')}")
    print(f"stale-source issues: {len(report.created)} opened, {len(report.closed)} closed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
