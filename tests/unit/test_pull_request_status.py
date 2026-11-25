"""Parsing the pull request status `gh` prints into its landing and check evidence."""

from __future__ import annotations

import json

import pytest

from shikiru.workspace.github import FailingCheck, PullRequestStatus, parse_pull_request_status

_HEAD_SHA = "9f1c2e4b7a6d5c3f2e1d0b9a8f7e6d5c4b3a2f1e"
_UNAVAILABLE = "check evidence unavailable"


def _status(**fields: object) -> PullRequestStatus:
    view: dict[str, object] = {
        "state": "OPEN",
        "mergeCommit": None,
        "mergeable": "MERGEABLE",
        "headRefOid": _HEAD_SHA,
        "statusCheckRollup": [],
        **fields,
    }
    return parse_pull_request_status(json.dumps(view))


def _run(status: object, conclusion: object, name: str = "ci") -> dict[str, object]:
    return {"__typename": "CheckRun", "name": name, "status": status, "conclusion": conclusion}


def _context(state: object) -> dict[str, object]:
    return {"__typename": "StatusContext", "context": "ci", "state": state}


@pytest.mark.parametrize(
    ("checks", "failing", "pending", "names", "error"),
    [
        pytest.param([_run("COMPLETED", "SUCCESS")], False, False, [], None, id="passing"),
        pytest.param(
            [_run("COMPLETED", "NEUTRAL"), _run("COMPLETED", "SKIPPED")],
            False,
            False,
            [],
            None,
            id="neutral-and-skipped",
        ),
        pytest.param([_run("QUEUED", None)], False, True, [], None, id="queued"),
        pytest.param([_run("IN_PROGRESS", None)], False, True, [], None, id="in-progress"),
        pytest.param([_run("WAITING", None)], False, True, [], None, id="waiting"),
        pytest.param([_run("PENDING", None)], False, True, [], None, id="pending"),
        pytest.param([_run("REQUESTED", None)], False, True, [], None, id="requested"),
        pytest.param(
            [_run("COMPLETED", "FAILURE", name="ci / lint"), _context("PENDING")],
            True,
            True,
            ["ci / lint"],
            None,
            id="pending-beside-a-settled-failure",
        ),
        pytest.param(
            [_run("COMPLETED", None)],
            True,
            False,
            [],
            "settled check has no result",
            id="settled-check-run-without-result",
        ),
        pytest.param(
            [_context(None)],
            True,
            False,
            [],
            "settled check has no result",
            id="settled-status-context-without-state",
        ),
        pytest.param(
            [_run("COMPLETED", "SOME_NEW_RESULT")],
            True,
            False,
            [],
            "unrecognized result",
            id="unrecognized-result",
        ),
        pytest.param(
            [_run("COMPLETED", "")], True, False, [], "unrecognized result", id="empty-result"
        ),
        pytest.param(
            [_run("COMPLETED", "PENDING")],
            True,
            False,
            [],
            "unrecognized result",
            id="pending-state-as-check-run-conclusion",
        ),
        pytest.param([_run(None, None)], True, False, [], "unrecognized status", id="null-status"),
        pytest.param(
            [_run("BOGUS", None)], True, False, [], "unrecognized status", id="unrecognized-status"
        ),
        pytest.param(
            [_run({"v": 1}, "SUCCESS")],
            True,
            False,
            [],
            "unrecognized status",
            id="non-string-status",
        ),
        pytest.param(
            [{"__typename": "CheckRun", "name": "ci", "conclusion": "SUCCESS"}],
            True,
            False,
            [],
            "status is missing",
            id="missing-status-with-success",
        ),
        pytest.param(
            [{"__typename": "CheckRun", "name": "ci", "conclusion": "FAILURE"}],
            True,
            False,
            ["ci"],
            "status is missing",
            id="missing-status-with-failure",
        ),
        pytest.param(
            [_context({"v": 1})],
            True,
            False,
            [],
            "state is not a result",
            id="non-string-state",
        ),
        pytest.param(None, True, False, [], "statusCheckRollup is missing", id="missing-rollup"),
    ],
)
def test_only_complete_readable_check_evidence_can_pass(
    checks: object, failing: bool, pending: bool, names: list[str], error: str | None
) -> None:
    status = _status(statusCheckRollup=checks)

    assert status.checks_failing is failing
    assert status.checks_pending is pending
    assert [check.name for check in status.failing_checks] == names
    if error is None:
        assert status.evidence_error is None
    else:
        assert status.evidence_error is not None
        assert status.evidence_error.startswith(_UNAVAILABLE)
        assert error in status.evidence_error


@pytest.mark.parametrize(
    ("entry", "expected", "readable"),
    [
        pytest.param(
            {
                "__typename": "StatusContext",
                "context": "ci-status",
                "state": "ERROR",
                "targetUrl": "https://example.com/status/2",
            },
            FailingCheck("ci-status", "ERROR", "https://example.com/status/2", "StatusContext"),
            True,
            id="status-context",
        ),
        pytest.param(
            _run("COMPLETED", "FAILURE", name="ci / test"),
            FailingCheck("ci / test", "FAILURE", None, "CheckRun"),
            True,
            id="no-details-link",
        ),
        pytest.param(
            {"__typename": "CheckRun", "status": "COMPLETED", "conclusion": "FAILURE"},
            FailingCheck("unknown check", "FAILURE", None, "CheckRun"),
            False,
            id="no-name",
        ),
    ],
)
def test_a_failing_check_keeps_its_name_result_link_and_kind(
    entry: dict[str, object], expected: FailingCheck, readable: bool
) -> None:
    status = _status(statusCheckRollup=[entry])

    assert status.checks_failing is True
    assert status.failing_checks == (expected,)
    assert (status.evidence_error is None) is readable


def test_a_conflict_is_reported_beside_passing_checks() -> None:
    status = _status(mergeable="CONFLICTING")

    assert status.conflicting is True
    assert status.checks_failing is False
    assert status.checks_pending is False


def test_a_missing_head_is_visible_and_never_passes() -> None:
    status = _status(headRefOid=None)

    assert status.head_sha is None
    assert status.checks_failing is True
    assert status.evidence_error is not None
    assert "head" in status.evidence_error
