"""How Shikiru retries a fault is told apart per scope, and only backoff retries need no action."""

from __future__ import annotations

import pytest

from shikiru.dashboard.fault_guidance import Recovery, recovery_of


@pytest.mark.parametrize(
    ("scope", "recoverable", "recovery"),
    [
        ("base push main", True, Recovery.BACKOFF),
        ("remote check origin", True, Recovery.BACKOFF),
        ("pull request close", True, Recovery.BOUNDED),
        ("reconcile resume", True, Recovery.STARTUP_ONLY),
        ("merge", True, Recovery.UNKNOWN),
        ("base push main", False, Recovery.UNRECOVERABLE),
    ],
    ids=[
        "push",
        "remote check",
        "pull request close",
        "reconcile",
        "unknown scope",
        "unrecoverable",
    ],
)
def test_each_scope_is_retried_in_its_own_way(
    scope: str, recoverable: bool, recovery: Recovery
) -> None:
    assert recovery_of(scope, recoverable=recoverable) is recovery
