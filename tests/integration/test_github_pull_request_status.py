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
