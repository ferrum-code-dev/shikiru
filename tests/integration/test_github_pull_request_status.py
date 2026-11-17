"""Pull request status through the subprocess boundary to a fake `gh`."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from shikiru.process import SubprocessRunner
from shikiru.workspace.github import GitHub, PullRequestState

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.workspace.github import PullRequestStatus
    from tests.support.github import FakeGitHub

pytestmark = pytest.mark.asyncio

_RUNNER = SubprocessRunner()
_HEAD_SHA = "9f1c2e4b7a6d5c3f2e1d0b9a8f7e6d5c4b3a2f1e"


async def _open_status(
    fake_github: FakeGitHub, tmp_path: Path, **fields: object
) -> PullRequestStatus:
    github = GitHub(_RUNNER)
    url = await github.create_pull_request(
        tmp_path, base="main", head="task-branch", title="title", body="body"
    )
    number = int(url.rsplit("/", 1)[-1])
    merged = {"headRefOid": _HEAD_SHA, **fields}
    fake_github.set_fields(number, **merged)
    status = await github.pull_request_status(tmp_path, url)
    assert status.state is PullRequestState.OPEN
    return status


async def test_failing_check_runs_expose_head_name_result_and_link(
    fake_github: FakeGitHub, tmp_path: Path
) -> None:
    status = await _open_status(
        fake_github,
        tmp_path,
        statusCheckRollup=[
            {
                "__typename": "CheckRun",
                "name": "ci / test",
                "status": "COMPLETED",
                "conclusion": "FAILURE",
                "detailsUrl": "https://example.com/runs/1",
            }
        ],
    )
    assert status.head_sha == _HEAD_SHA
    assert status.checks_failing is True
    assert status.checks_pending is False
    assert status.conflicting is False
    assert status.evidence_error is None
    assert len(status.failing_checks) == 1
    assert status.failing_checks[0].name == "ci / test"
    assert status.failing_checks[0].result == "FAILURE"
    assert status.failing_checks[0].details_url == "https://example.com/runs/1"
    assert status.failing_checks[0].kind == "CheckRun"


async def test_failing_status_contexts_expose_context_and_target(
    fake_github: FakeGitHub, tmp_path: Path
) -> None:
    status = await _open_status(
        fake_github,
        tmp_path,
        statusCheckRollup=[
            {
                "__typename": "StatusContext",
                "context": "ci-status",
                "state": "ERROR",
                "targetUrl": "https://example.com/status/2",
            }
        ],
    )
    assert status.checks_failing is True
    assert status.checks_pending is False
    assert status.failing_checks[0].name == "ci-status"
    assert status.failing_checks[0].result == "ERROR"
    assert status.failing_checks[0].details_url == "https://example.com/status/2"
    assert status.failing_checks[0].kind == "StatusContext"


async def test_pending_check_blocks_a_settled_failure(
    fake_github: FakeGitHub, tmp_path: Path
) -> None:
    status = await _open_status(
        fake_github,
        tmp_path,
        statusCheckRollup=[
            {
                "__typename": "CheckRun",
                "name": "ci / lint",
                "status": "COMPLETED",
                "conclusion": "FAILURE",
            },
            {
                "__typename": "StatusContext",
                "context": "ci / test",
                "state": "PENDING",
            },
        ],
    )
    assert status.checks_failing is True
    assert status.checks_pending is True
    assert [check.name for check in status.failing_checks] == ["ci / lint"]


@pytest.mark.parametrize(
    "running_status", ["QUEUED", "IN_PROGRESS", "WAITING", "PENDING", "REQUESTED"]
)
async def test_running_check_run_is_pending_not_failing(
    fake_github: FakeGitHub, tmp_path: Path, running_status: str
) -> None:
    status = await _open_status(
        fake_github,
        tmp_path,
        statusCheckRollup=[
            {
                "__typename": "CheckRun",
                "name": "ci / test",
                "status": running_status,
                "conclusion": None,
            }
        ],
    )
    assert status.checks_failing is False
    assert status.checks_pending is True
    assert status.failing_checks == ()
    assert status.evidence_error is None


async def test_passing_check_is_neither_failing_nor_pending(
    fake_github: FakeGitHub, tmp_path: Path
) -> None:
    status = await _open_status(
        fake_github,
        tmp_path,
        statusCheckRollup=[
            {
                "__typename": "CheckRun",
                "name": "ci / test",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
            }
        ],
    )
    assert status.checks_failing is False
    assert status.checks_pending is False
    assert status.failing_checks == ()
    assert status.evidence_error is None


async def test_conflict_is_preserved_with_check_evidence(
    fake_github: FakeGitHub, tmp_path: Path
) -> None:
    status = await _open_status(
        fake_github, tmp_path, mergeable="CONFLICTING", statusCheckRollup=[]
    )
    assert status.conflicting is True
    assert status.checks_failing is False
    assert status.checks_pending is False


async def test_missing_head_is_visible_and_not_passing(
    fake_github: FakeGitHub, tmp_path: Path
) -> None:
    status = await _open_status(fake_github, tmp_path, headRefOid=None, statusCheckRollup=[])
    assert status.head_sha is None
    assert status.checks_failing is True
    assert status.evidence_error is not None
    assert "head" in status.evidence_error


async def test_missing_rollup_is_visible_and_not_passing(
    fake_github: FakeGitHub, tmp_path: Path
) -> None:
    status = await _open_status(fake_github, tmp_path, statusCheckRollup=None)
    assert status.checks_failing is True
    assert status.evidence_error is not None
    assert status.failing_checks == ()


async def test_missing_name_is_unknown_and_visible(fake_github: FakeGitHub, tmp_path: Path) -> None:
    status = await _open_status(
        fake_github,
        tmp_path,
        statusCheckRollup=[
            {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "FAILURE"}
        ],
    )
    assert status.checks_failing is True
    assert status.evidence_error is not None
    assert status.failing_checks[0].name == "unknown check"
    assert status.failing_checks[0].kind == "CheckRun"
    assert status.failing_checks[0].details_url is None


async def test_missing_details_link_is_unavailable_not_an_error(
    fake_github: FakeGitHub, tmp_path: Path
) -> None:
    status = await _open_status(
        fake_github,
        tmp_path,
        statusCheckRollup=[
            {
                "__typename": "CheckRun",
                "name": "ci / test",
                "status": "COMPLETED",
                "conclusion": "FAILURE",
            }
        ],
    )
    assert status.checks_failing is True
    assert status.checks_pending is False
    assert status.evidence_error is None
    assert status.failing_checks[0].details_url is None


async def test_settled_check_run_without_result_is_visible_not_passing(
    fake_github: FakeGitHub, tmp_path: Path
) -> None:
    status = await _open_status(
        fake_github,
        tmp_path,
        statusCheckRollup=[
            {
                "__typename": "CheckRun",
                "name": "ci / test",
                "status": "COMPLETED",
                "conclusion": None,
            }
        ],
    )
    assert status.checks_failing is True
    assert status.checks_pending is False
    assert status.evidence_error is not None
    assert "settled check has no result" in status.evidence_error
    assert status.failing_checks == ()


async def test_settled_status_context_without_state_is_visible_not_passing(
    fake_github: FakeGitHub, tmp_path: Path
) -> None:
    status = await _open_status(
        fake_github,
        tmp_path,
        statusCheckRollup=[{"__typename": "StatusContext", "context": "ci", "state": None}],
    )
    assert status.checks_failing is True
    assert status.checks_pending is False
    assert status.evidence_error is not None
    assert "settled check has no result" in status.evidence_error


@pytest.mark.parametrize(
    "entry",
    [
        pytest.param(
            {"__typename": "CheckRun", "name": "ci", "status": None, "conclusion": None},
            id="null-status-without-result",
        ),
        pytest.param(
            {"__typename": "CheckRun", "name": "ci", "status": "BOGUS", "conclusion": None},
            id="unrecognized-status-without-result",
        ),
        pytest.param(
            {"__typename": "CheckRun", "name": "ci", "conclusion": "SUCCESS"},
            id="missing-status-with-success",
        ),
        pytest.param(
            {"__typename": "CheckRun", "name": "ci", "conclusion": "FAILURE"},
            id="missing-status-with-failure",
        ),
        pytest.param(
            {"__typename": "CheckRun", "name": "ci", "status": {"v": 1}, "conclusion": "SUCCESS"},
            id="non-string-status",
        ),
        pytest.param(
            {"__typename": "StatusContext", "context": "ci", "state": {"v": 1}},
            id="non-string-state",
        ),
        pytest.param(
            {
                "__typename": "CheckRun",
                "name": "ci",
                "status": "COMPLETED",
                "conclusion": "PENDING",
            },
            id="pending-state-as-check-run-conclusion",
        ),
    ],
)
async def test_malformed_lifecycle_or_result_is_visible_not_passing(
    fake_github: FakeGitHub, tmp_path: Path, entry: dict[str, object]
) -> None:
    status = await _open_status(fake_github, tmp_path, statusCheckRollup=[entry])
    assert status.checks_failing is True
    assert status.checks_pending is False
    assert status.evidence_error is not None


async def test_unrecognized_result_is_visible_not_passing(
    fake_github: FakeGitHub, tmp_path: Path
) -> None:
    status = await _open_status(
        fake_github,
        tmp_path,
        statusCheckRollup=[
            {
                "__typename": "CheckRun",
                "name": "ci / test",
                "status": "COMPLETED",
                "conclusion": "SOME_NEW_RESULT",
            }
        ],
    )
    assert status.checks_failing is True
    assert status.checks_pending is False
    assert status.evidence_error is not None
    assert "unrecognized result" in status.evidence_error
    assert status.failing_checks == ()


async def test_empty_result_is_visible_not_passing(fake_github: FakeGitHub, tmp_path: Path) -> None:
    status = await _open_status(
        fake_github,
        tmp_path,
        statusCheckRollup=[
            {
                "__typename": "CheckRun",
                "name": "ci / test",
                "status": "COMPLETED",
                "conclusion": "",
            }
        ],
    )
    assert status.checks_failing is True
    assert status.checks_pending is False
    assert status.evidence_error is not None
    assert "unrecognized result" in status.evidence_error


async def test_neutral_and_skipped_results_pass_without_error(
    fake_github: FakeGitHub, tmp_path: Path
) -> None:
    status = await _open_status(
        fake_github,
        tmp_path,
        statusCheckRollup=[
            {
                "__typename": "CheckRun",
                "name": "ci / neutral",
                "status": "COMPLETED",
                "conclusion": "NEUTRAL",
            },
            {
                "__typename": "CheckRun",
                "name": "ci / skipped",
                "status": "COMPLETED",
                "conclusion": "SKIPPED",
            },
        ],
    )
    assert status.checks_failing is False
    assert status.checks_pending is False
    assert status.evidence_error is None
