"""scripts/sync_issues.py against a fake GitHub (spec 11.1, Appendix C 83). No network, no real token."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from scripts.sync_issues import (
    COOLDOWN,
    LABEL,
    MAX_CREATE,
    TITLE_PREFIX,
    GithubError,
    Issues,
    Row,
    existing,
    main,
    parse_rows,
    run_url_from,
    sync,
)

TOKEN = "ghs_UNIQUE_issue_sync_token_77aa"
NOW = datetime(2026, 10, 20, 6, 0, tzinfo=UTC)


def row(
    sid: str = "hn-ai", *, stale: bool = True, days: float = 3.2, threshold: int = 3,
    status: str = "empty",
) -> Row:  # fmt: skip
    return Row(id=sid, status=status, empty_days=days, threshold=threshold, stale=stale)


def issue(
    number: int, source: str, *, state: str = "open", closed_days_ago: float | None = None
) -> dict[str, Any]:
    closed = None if closed_days_ago is None else (NOW - timedelta(days=closed_days_ago)).isoformat()
    return {"number": number, "title": f"{TITLE_PREFIX}{source}", "state": state, "closed_at": closed}


class FakeGitHub:
    """Just enough of the Issues API. `fail` makes every request whose 'METHOD path' contains
    `fail_on` answer with that status and a body that echoes the token (it must never surface)."""

    def __init__(
        self, issues: list[dict[str, Any]] | None = None, *, fail: int | None = None,
        fail_on: str = "",
    ) -> None:  # fmt: skip
        self.issues = issues if issues is not None else []
        self.requests: list[httpx.Request] = []
        self.bodies: list[Any] = []
        self.fail, self.fail_on = fail, fail_on

    def calls(self) -> list[str]:
        return [f"{r.method} {r.url.path}" for r in self.requests]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = json.loads(request.content) if request.content else None
        self.bodies.append(body)
        key = f"{request.method} {request.url.path}"
        if self.fail is not None and self.fail_on in key:
            return httpx.Response(self.fail, json={"message": f"denied for token {TOKEN}"})
        if key == "GET /repos/o/r/issues":
            return httpx.Response(200, json=self.issues)
        if key == "POST /repos/o/r/issues":
            number = max([i["number"] for i in self.issues], default=0) + 1
            created = {
                "number": number, "title": body["title"], "state": "open", "closed_at": None,
                "body": body["body"],
            }  # fmt: skip
            self.issues.append(created)
            return httpx.Response(201, json=created)
        match = re.fullmatch(r"/repos/o/r/issues/(\d+)(/comments)?", request.url.path)
        if match and request.method == "POST" and match.group(2):
            return httpx.Response(201, json={"id": 1})
        if match and request.method == "PATCH":
            for item in self.issues:
                if item["number"] == int(match.group(1)):
                    item.update(state="closed", closed_at=NOW.isoformat())
            return httpx.Response(200, json={})
        return httpx.Response(404, json={})

    def issues_api(self) -> Issues:
        return Issues(httpx.Client(transport=httpx.MockTransport(self.handler)), "o/r", TOKEN)

    def posts_to_issues(self) -> int:
        return self.calls().count("POST /repos/o/r/issues")


# ---- creating -----------------------------------------------------------------------------


def test_a_stale_source_gets_one_labelled_issue_with_a_safe_body() -> None:
    fake = FakeGitHub()
    report = sync([row()], fake.issues_api(), NOW)
    assert report.created == ["hn-ai"] and report.closed == [] and report.warnings == []
    assert fake.calls() == ["GET /repos/o/r/issues", "POST /repos/o/r/issues"]
    body = fake.bodies[1]
    assert body["title"] == "Stale source: hn-ai" and body["labels"] == [LABEL]
    for needle in ("`hn-ai`", "3.2 active days", "threshold: 3", "Last status: empty"):
        assert needle in body["body"]
    assert "Collect run" not in body["body"]


def test_the_run_link_is_added_when_given() -> None:
    fake = FakeGitHub()
    sync([row()], fake.issues_api(), NOW, "https://github.com/o/r/actions/runs/42")
    assert "Collect run: https://github.com/o/r/actions/runs/42" in fake.bodies[1]["body"]


def test_a_second_run_does_not_duplicate_the_issue() -> None:
    fake = FakeGitHub()
    sync([row()], fake.issues_api(), NOW)
    again = sync([row()], fake.issues_api(), NOW)
    assert again.created == [] and fake.posts_to_issues() == 1 and len(fake.issues) == 1


def test_creation_is_capped_per_run() -> None:
    fake = FakeGitHub()
    report = sync([row(f"src-{i}") for i in range(MAX_CREATE + 2)], fake.issues_api(), NOW)
    assert len(report.created) == MAX_CREATE and fake.posts_to_issues() == MAX_CREATE
    assert report.warnings == ["issues: creation cap reached for this run"]


@pytest.mark.parametrize(("days_ago", "reopened"), [(1, False), (2.9, False), (3, True), (4, True)])
def test_a_manually_closed_issue_is_not_reopened_inside_the_cooldown(
    days_ago: float, reopened: bool
) -> None:
    assert COOLDOWN == timedelta(days=3)
    fake = FakeGitHub([issue(5, "hn-ai", state="closed", closed_days_ago=days_ago)])
    report = sync([row()], fake.issues_api(), NOW)
    assert (report.created == ["hn-ai"]) is reopened


# ---- closing ------------------------------------------------------------------------------


def test_a_recovered_source_gets_a_comment_and_is_closed() -> None:
    fake = FakeGitHub([issue(7, "hn-ai")])
    report = sync([row(stale=False)], fake.issues_api(), NOW)
    assert report.closed == ["hn-ai"] and report.created == []
    assert fake.calls() == [
        "GET /repos/o/r/issues", "POST /repos/o/r/issues/7/comments", "PATCH /repos/o/r/issues/7",
    ]  # fmt: skip
    assert fake.bodies[2] == {"state": "closed", "state_reason": "completed"}
    assert fake.issues[0]["state"] == "closed"


def test_every_open_duplicate_is_closed() -> None:
    fake = FakeGitHub([issue(7, "hn-ai"), issue(9, "hn-ai")])
    sync([row(stale=False)], fake.issues_api(), NOW)
    assert "PATCH /repos/o/r/issues/7" in fake.calls()
    assert "PATCH /repos/o/r/issues/9" in fake.calls()


def test_a_healthy_source_without_an_issue_changes_nothing() -> None:
    fake = FakeGitHub()
    sync([row(stale=False)], fake.issues_api(), NOW)
    assert fake.calls() == ["GET /repos/o/r/issues"]


def test_an_issue_for_a_source_that_is_not_in_the_report_is_left_alone() -> None:
    fake = FakeGitHub([issue(7, "removed-source")])
    sync([row("hn-ai", stale=False)], fake.issues_api(), NOW)
    assert fake.calls() == ["GET /repos/o/r/issues"]


def test_pull_requests_and_foreign_titles_are_never_matched_or_touched() -> None:
    foreign: list[Any] = [
        {**issue(1, "hn-ai"), "pull_request": {}},
        {"number": 2, "title": "Stale source: ../x", "state": "open", "closed_at": None},
        {"number": 3, "title": "Stale source: hn-ai\nmore", "state": "open", "closed_at": None},
        {"number": 4, "title": "Something else", "state": "open", "closed_at": None},
        {"number": True, "title": "Stale source: hn-ai", "state": "open", "closed_at": None},
        "not an object",
    ]
    assert existing(foreign) == []
    created = FakeGitHub(list(foreign))
    assert sync([row()], created.issues_api(), NOW).created == ["hn-ai"]
    healthy = FakeGitHub(list(foreign))
    sync([row(stale=False)], healthy.issues_api(), NOW)
    assert not [c for c in healthy.calls() if c.startswith("PATCH")]


# ---- failures never leak and never loop ---------------------------------------------------


def test_a_failed_listing_is_a_warning_with_the_status_only() -> None:
    fake = FakeGitHub(fail=403)
    report = sync([row()], fake.issues_api(), NOW)
    assert report.warnings == ["issues: GitHub API returned HTTP 403"] and report.created == []
    assert len(fake.requests) == 1
    assert TOKEN not in repr(report)


def test_a_failed_creation_stops_the_remaining_calls() -> None:
    fake = FakeGitHub(fail=403, fail_on="POST /repos/o/r/issues")
    report = sync([row("a-one"), row("b-two")], fake.issues_api(), NOW)
    assert report.created == [] and len(report.warnings) == 1
    assert fake.posts_to_issues() == 1 and TOKEN not in repr(report)


def test_a_failed_comment_means_the_issue_is_not_closed() -> None:
    fake = FakeGitHub([issue(7, "hn-ai")], fail=500, fail_on="/comments")
    report = sync([row(stale=False)], fake.issues_api(), NOW)
    assert report.closed == [] and len(report.warnings) == 1
    assert not [c for c in fake.calls() if c.startswith("PATCH")]


def test_a_network_error_is_a_warning_without_details() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {TOKEN}", request=request)

    api = Issues(httpx.Client(transport=httpx.MockTransport(boom)), "o/r", TOKEN)
    report = sync([row()], api, NOW)
    assert report.warnings == ["issues: GitHub API unreachable"] and TOKEN not in repr(report)


def test_a_redirect_is_an_error_and_is_not_followed() -> None:
    seen: list[httpx.Request] = []

    def redirect(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(302, headers={"location": "https://evil.example/steal"})

    api = Issues(httpx.Client(transport=httpx.MockTransport(redirect)), "o/r", TOKEN)
    report = sync([row()], api, NOW)
    assert report.warnings == ["issues: GitHub API returned HTTP 302"]
    assert len(seen) == 1 and seen[0].url.host == "api.github.com"


@pytest.mark.parametrize(
    "answer", [httpx.Response(200, text="not json"), httpx.Response(200, json={"x": 1})]
)
def test_an_unusable_answer_is_a_502_warning(answer: httpx.Response) -> None:
    api = Issues(httpx.Client(transport=httpx.MockTransport(lambda r: answer)), "o/r", TOKEN)
    assert sync([row()], api, NOW).warnings == ["issues: GitHub API returned HTTP 502"]
    with pytest.raises(GithubError):
        existing({"not": "a list"})


def test_every_request_goes_to_the_api_host_with_the_token_in_the_header_only() -> None:
    fake = FakeGitHub([issue(7, "old-one")])
    sync([row(), row("old-one", stale=False)], fake.issues_api(), NOW)
    assert len(fake.requests) >= 4
    for request in fake.requests:
        assert request.url.scheme == "https" and request.url.host == "api.github.com"
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert request.headers["x-github-api-version"] == "2022-11-28"
        assert TOKEN not in str(request.url) and TOKEN not in request.content.decode()
    params = fake.requests[0].url.params
    assert (params["labels"], params["state"], params["per_page"]) == (LABEL, "all", "100")


@pytest.mark.parametrize("repo", ["", "not a repo", "a/b/c", "a/b; rm -rf", "a/b\n", "../x/y"])
def test_a_bad_repository_name_is_refused(repo: str) -> None:
    with pytest.raises(ValueError):
        Issues(httpx.Client(), repo, TOKEN)


# ---- reading meta.json --------------------------------------------------------------------


def test_parse_rows_is_tolerant_and_trusts_no_text() -> None:
    meta = {"sources": [
        {"id": "hn-ai", "status": "empty", "empty_days": 3.5, "stale_after_days": 5, "stale": True},
        {"id": "Bad_ID", "stale": True}, "text", {"id": "x" * 65, "stale": True},
        {"id": "ok\n", "stale": True},
        {"id": "odd", "status": "<script>", "empty_days": "3", "stale_after_days": 99, "stale": "yes"},
        {"id": "bools", "empty_days": True, "stale_after_days": True, "stale": True},
    ]}  # fmt: skip
    parsed = {r.id: r for r in parse_rows(meta)}
    assert set(parsed) == {"hn-ai", "odd", "bools"}
    assert parsed["hn-ai"] == Row("hn-ai", "empty", 3.5, 5, True)
    assert parsed["odd"] == Row("odd", "unknown", 0.0, 3, False)
    assert parsed["bools"] == Row("bools", "unknown", 0.0, 3, True)
    assert parse_rows([]) == [] and parse_rows({"sources": "x"}) == [] and parse_rows(None) == []


def test_run_url_is_built_only_from_validated_parts() -> None:
    good = {"GITHUB_SERVER_URL": "https://github.com/", "GITHUB_REPOSITORY": "o/r", "GITHUB_RUN_ID": "42"}
    assert run_url_from(good) == "https://github.com/o/r/actions/runs/42"
    for bad in (
        {"GITHUB_SERVER_URL": "http://github.com"}, {"GITHUB_SERVER_URL": "https://u:p@github.com"},
        {"GITHUB_RUN_ID": "4x"}, {"GITHUB_REPOSITORY": "o/r/extra"}, {"GITHUB_RUN_ID": ""},
    ):  # fmt: skip
        assert run_url_from({**good, **bad}) == ""


# ---- the command line ---------------------------------------------------------------------

ENV = {
    "GITHUB_REPOSITORY": "o/r", "GH_TOKEN": TOKEN, "GITHUB_SERVER_URL": "https://github.com",
    "GITHUB_RUN_ID": "42",
}  # fmt: skip


def write_meta(tmp_path: Path, rows: list[dict[str, Any]]) -> Path:
    path = tmp_path / "meta.json"
    path.write_text(json.dumps({"sources": rows}), encoding="utf-8")
    return path


def stale_row(sid: str = "hn-ai") -> dict[str, Any]:
    return {"id": sid, "status": "empty", "empty_days": 3.2, "stale_after_days": 3, "stale": True}


def test_main_opens_the_issue_and_prints_a_summary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeGitHub()
    client = httpx.Client(transport=httpx.MockTransport(fake.handler))
    meta = write_meta(tmp_path, [stale_row()])
    assert main(["--meta", str(meta)], env=ENV, client=client, now=NOW) == 0
    assert "1 opened, 0 closed" in capsys.readouterr().out
    assert "https://github.com/o/r/actions/runs/42" in fake.bodies[1]["body"]


def test_main_exits_0_with_a_workflow_warning_when_github_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeGitHub(fail=403)
    client = httpx.Client(transport=httpx.MockTransport(fake.handler))
    meta = write_meta(tmp_path, [stale_row()])
    assert main(["--meta", str(meta)], env=ENV, client=client, now=NOW) == 0
    captured = capsys.readouterr()
    assert "::warning::issues: GitHub API returned HTTP 403" in captured.out
    assert TOKEN not in captured.out + captured.err


def test_the_token_fallback_name_works(tmp_path: Path) -> None:
    fake = FakeGitHub()
    client = httpx.Client(transport=httpx.MockTransport(fake.handler))
    env = {"GITHUB_REPOSITORY": "o/r", "GITHUB_TOKEN": TOKEN}
    meta = write_meta(tmp_path, [stale_row()])
    assert main(["--meta", str(meta)], env=env, client=client, now=NOW) == 0
    assert fake.requests and fake.requests[0].headers["authorization"] == f"Bearer {TOKEN}"


def test_dry_run_calls_nothing_and_needs_no_credentials(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbidden(request: httpx.Request) -> httpx.Response:
        raise AssertionError("a dry run must not call GitHub")

    client = httpx.Client(transport=httpx.MockTransport(forbidden))
    meta = write_meta(tmp_path, [stale_row(), {"id": "ok-source", "stale": False}])
    assert main(["--meta", str(meta), "--dry-run"], env={}, client=client) == 0
    assert "dry run: 1 stale source(s): hn-ai" in capsys.readouterr().out


def test_unusable_input_exits_2_and_names_variables_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeGitHub()
    client = httpx.Client(transport=httpx.MockTransport(fake.handler))
    meta = write_meta(tmp_path, [stale_row()])
    assert main(["--meta", str(tmp_path / "missing.json")], env=ENV, client=client) == 2
    assert main(["--meta", str(tmp_path)], env=ENV, client=client) == 2  # a directory
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert main(["--meta", str(bad)], env=ENV, client=client) == 2
    assert main(["--meta", str(meta)], env={**ENV, "GITHUB_REPOSITORY": "x"}, client=client) == 2
    assert main(["--meta", str(meta)], env={"GITHUB_REPOSITORY": "o/r"}, client=client) == 2
    err = capsys.readouterr().err
    assert "GITHUB_REPOSITORY" in err and "GH_TOKEN" in err and TOKEN not in err
    assert fake.requests == []  # none of these reached GitHub


def test_a_report_with_nothing_stale_only_reads_the_issue_list(tmp_path: Path) -> None:
    fake = FakeGitHub()
    client = httpx.Client(transport=httpx.MockTransport(fake.handler))
    meta = write_meta(tmp_path, [{"id": "hn-ai", "stale": False, "status": "ok"}])
    assert main(["--meta", str(meta)], env=ENV, client=client, now=NOW) == 0
    assert fake.calls() == ["GET /repos/o/r/issues"]
