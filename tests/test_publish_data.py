"""scripts/publish_data.py against a local bare repository (no network)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.publish_data import GitError, _auth_args, git, main, prepare, push, scrub

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def run(cwd: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return out.stdout.strip()


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    path = tmp_path / "remote.git"
    run(tmp_path, "init", "-q", "--bare", str(path))
    return path


def write(root: Path, rel: str, text: str) -> None:
    f = root / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")


def remote_count(remote: Path) -> int:
    return int(run(remote, "rev-list", "--count", "data"))


def remote_files(remote: Path) -> set[str]:
    return set(run(remote, "ls-tree", "-r", "--name-only", "data").splitlines())


def test_first_run_creates_the_branch_then_updates_and_skips_unchanged(
    tmp_path: Path, remote: Path
) -> None:
    url = str(remote)
    ws1 = tmp_path / "run1"
    assert prepare(ws1, url) == "new"
    write(ws1, "feed/meta.json", "{}")
    assert push(ws1, url) == "committed"
    assert remote_files(remote) == {".nojekyll", "feed/meta.json"} and remote_count(remote) == 1

    ws2 = tmp_path / "run2"  # a later run starts from a fresh checkout of the branch
    assert prepare(ws2, url) == "existing"
    assert (ws2 / "feed" / "meta.json").read_text(encoding="utf-8") == "{}"
    assert push(ws2, url) == "unchanged" and remote_count(remote) == 1
    write(ws2, "feed/meta.json", '{"n": 2}')
    write(ws2, "store.json", "{}")
    assert push(ws2, url) == "committed" and remote_count(remote) == 2
    assert "store.json" in remote_files(remote)


def test_history_is_squashed_once_the_threshold_is_reached(tmp_path: Path, remote: Path) -> None:
    url = str(remote)
    for i in range(3):
        ws = tmp_path / f"run{i}"
        prepare(ws, url, squash_after=3)
        write(ws, "feed/meta.json", f'{{"n": {i}}}')
        assert push(ws, url, squash_after=3) == "committed"
    assert remote_count(remote) == 3
    ws = tmp_path / "run-squash"
    prepare(ws, url, squash_after=3)
    write(ws, "feed/meta.json", '{"n": 99}')
    assert push(ws, url, squash_after=3) == "squashed"
    assert remote_count(remote) == 1
    assert run(remote, "show", "data:feed/meta.json") == '{"n": 99}'
    ws_next = tmp_path / "run-after"
    assert prepare(ws_next, url, squash_after=3) == "existing"
    write(ws_next, "feed/meta.json", '{"n": 100}')
    assert push(ws_next, url, squash_after=3) == "committed" and remote_count(remote) == 2


def test_squash_is_seen_even_through_the_shallow_fetch(tmp_path: Path, remote: Path) -> None:
    url = str(remote)
    ws = tmp_path / "seed"
    prepare(ws, url, squash_after=6)
    for i in range(10):  # more commits than the fetch depth (squash_after + 4 = 10 is the cap)
        write(ws, "feed/meta.json", f'{{"n": {i}}}')
        push(ws, url, squash_after=100)
    later = tmp_path / "later"
    prepare(later, url, squash_after=6)
    write(later, "feed/meta.json", '{"n": "x"}')
    assert push(later, url, squash_after=6) == "squashed"


def test_a_failing_remote_is_an_error_not_a_new_branch(tmp_path: Path) -> None:
    with pytest.raises(GitError, match="ls-remote"):
        prepare(tmp_path / "ws", str(tmp_path / "does-not-exist.git"))


def test_token_is_only_a_header_and_never_in_messages() -> None:
    token = "ghs_SECRETTOKEN123"
    assert _auth_args("/tmp/local.git", token) == []  # local remotes get no header
    args = _auth_args("https://github.com/o/r.git", token)
    assert args[0] == "-c" and "extraheader=AUTHORIZATION: basic " in args[1]
    assert token not in args[1]  # base64-encoded in the header, never plain
    assert _auth_args("https://github.com/o/r.git", None) == []
    basic = args[1].rsplit(" ", 1)[1]
    assert scrub(f"fatal: {token} {basic}", token) == "fatal: *** ***"


def test_git_errors_do_not_leak_the_token(tmp_path: Path) -> None:
    token = "ghs_SECRETTOKEN123"
    tmp_path.mkdir(exist_ok=True)
    with pytest.raises(GitError) as exc:
        git(tmp_path, "frobnicate", remote="https://github.com/o/r.git", token=token)
    assert token not in str(exc.value)


def test_cli_round_trip_and_failure_exit_code(
    tmp_path: Path, remote: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ws = tmp_path / "cli"
    assert main(["prepare", "--data-dir", str(ws), "--remote", str(remote)]) == 0
    write(ws, "feed/meta.json", "{}")
    assert main(["push", "--data-dir", str(ws), "--remote", str(remote)]) == 0
    assert "data branch: committed" in capsys.readouterr().out
    assert (
        main(["prepare", "--data-dir", str(tmp_path / "x"), "--remote", str(tmp_path / "no")]) == 1
    )
