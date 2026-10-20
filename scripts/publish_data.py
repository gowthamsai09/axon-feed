"""Publish the feed to the `data` branch (spec 5.5), used by .github/workflows/collect.yml.

  python -m scripts.publish_data prepare --data-dir data --remote URL
  python -m pipeline.run --out data/feed --window-hours 168
  python -m scripts.publish_data push    --data-dir data --remote URL

prepare: puts the previous run's output (feed/, store.json) into data/. The first run, when the
branch does not exist yet, starts an empty orphan branch.
push: commits what changed and pushes. Once the branch holds SQUASH_AFTER commits (about a week of
runs, 8 a day) the history is replaced by one orphan commit and force-pushed, so the repo does not
grow without bound. A .nojekyll file is always kept so Pages serves the files as they are.

The token (env GH_TOKEN, optional) is sent as an HTTP header for https remotes only, is never part
of a URL or a stored remote, and is scrubbed from error messages."""

from __future__ import annotations

import argparse
import base64
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

BRANCH = "data"
SQUASH_AFTER = 56  # commits: 8 runs a day for 7 days
BOT_NAME, BOT_EMAIL = "feed-bot", "feed-bot@users.noreply.github.com"


class GitError(RuntimeError):
    pass


def _auth_args(remote: str, token: str | None) -> list[str]:
    if not token or not remote.startswith("https://"):
        return []
    host = remote.split("/")[2]
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return ["-c", f"http.https://{host}/.extraheader=AUTHORIZATION: basic {basic}"]


def scrub(text: str, token: str | None) -> str:
    if not token:
        return text
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return text.replace(basic, "***").replace(token, "***")


def git(
    data_dir: Path,
    *args: str,
    remote: str = "",
    token: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    cmd = [
        "git",
        "-c",
        f"user.name={BOT_NAME}",
        "-c",
        f"user.email={BOT_EMAIL}",
        "-c",
        "commit.gpgsign=false",
        *_auth_args(remote, token),
        *args,
    ]
    proc = subprocess.run(cmd, cwd=data_dir, capture_output=True, text=True, check=False)
    if check and proc.returncode != 0:
        detail = scrub((proc.stderr or proc.stdout).strip(), token)[:500]
        raise GitError(f"git {args[0]} failed ({proc.returncode}): {detail}")
    return proc


def prepare(
    data_dir: Path,
    remote: str,
    *,
    branch: str = BRANCH,
    token: str | None = None,
    squash_after: int = SQUASH_AFTER,
) -> str:
    """'existing' when the branch was fetched, 'new' when it starts empty (first run)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    if not (data_dir / ".git").exists():
        git(data_dir, "init", "-q")
    probe = git(
        data_dir, "ls-remote", "--exit-code", "--heads", remote, branch,
        remote=remote, token=token, check=False,
    )  # fmt: skip
    if probe.returncode == 0:
        # depth just above the squash threshold, so push() can still see that it is time to squash
        git(
            data_dir, "fetch", "-q", f"--depth={squash_after + 4}", remote,
            f"refs/heads/{branch}", remote=remote, token=token,
        )  # fmt: skip
        git(data_dir, "checkout", "-q", "-B", branch, "FETCH_HEAD")
        return "existing"
    if probe.returncode != 2:  # 2 = the branch is not there; anything else is a real failure
        detail = scrub((probe.stderr or probe.stdout).strip(), token)[:300]
        raise GitError(f"git ls-remote failed ({probe.returncode}): {detail}")
    git(data_dir, "checkout", "-q", "--orphan", branch)
    return "new"


def push(
    data_dir: Path,
    remote: str,
    *,
    branch: str = BRANCH,
    token: str | None = None,
    squash_after: int = SQUASH_AFTER,
    now: datetime | None = None,
) -> str:
    """'unchanged', 'committed' or 'squashed'."""
    stamp = (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    (data_dir / ".nojekyll").touch()
    git(data_dir, "add", "-A")
    head = git(data_dir, "rev-list", "--count", "HEAD", check=False)
    commits = int(head.stdout.strip()) if head.returncode == 0 else 0
    changed = git(data_dir, "diff", "--cached", "--quiet", check=False).returncode == 1
    ref = f"HEAD:refs/heads/{branch}"
    if commits >= squash_after:
        git(data_dir, "checkout", "-q", "--orphan", "_squashed")
        git(data_dir, "add", "-A")
        git(data_dir, "commit", "-q", "-m", f"feed snapshot {stamp} (history squashed)")
        git(data_dir, "branch", "-M", branch)
        git(data_dir, "push", "-q", "--force", remote, ref, remote=remote, token=token)
        return "squashed"
    if not changed and commits > 0:
        return "unchanged"
    git(data_dir, "commit", "-q", "-m", f"feed update {stamp}")
    git(data_dir, "push", "-q", remote, ref, remote=remote, token=token)
    return "committed"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("action", choices=["prepare", "push"])
    ap.add_argument("--data-dir", type=Path, default=Path("data"))
    ap.add_argument("--remote", required=True, help="e.g. https://github.com/<owner>/axon-feed.git")
    ap.add_argument("--branch", default=BRANCH)
    ap.add_argument("--squash-after", type=int, default=SQUASH_AFTER)
    args = ap.parse_args(argv)
    token = os.environ.get("GH_TOKEN") or None
    try:
        if args.action == "prepare":
            kind = prepare(
                args.data_dir, args.remote, branch=args.branch, token=token,
                squash_after=args.squash_after,
            )  # fmt: skip
            print(f"data branch: {kind}")
        else:
            result = push(
                args.data_dir, args.remote, branch=args.branch, token=token,
                squash_after=args.squash_after,
            )  # fmt: skip
            print(f"data branch: {result}")
    except GitError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
