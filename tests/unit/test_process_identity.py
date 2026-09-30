from __future__ import annotations

import pytest

from shikiru.process import START_TIME_TOLERANCE_SECONDS, ProcessIdentity

_PID = 4242
_STARTED_AT = 1_700_000_000.0
_BEYOND = START_TIME_TOLERANCE_SECONDS + 1.0


@pytest.mark.parametrize(
    ("live", "expected"),
    [
        (ProcessIdentity(_PID, _STARTED_AT), True),
        (ProcessIdentity(_PID, _STARTED_AT + START_TIME_TOLERANCE_SECONDS / 2), True),
        (ProcessIdentity(_PID, _STARTED_AT - START_TIME_TOLERANCE_SECONDS / 2), True),
        (ProcessIdentity(_PID, _STARTED_AT + _BEYOND), False),
        (ProcessIdentity(_PID, _STARTED_AT - _BEYOND), False),
        (ProcessIdentity(_PID + 1, _STARTED_AT), False),
        (None, False),
    ],
    ids=[
        "equal",
        "later-within",
        "earlier-within",
        "later-beyond",
        "earlier-beyond",
        "other-pid",
        "gone",
    ],
)
def test_a_live_process_matches_the_recorded_identity_within_the_tolerance(
    live: ProcessIdentity | None, *, expected: bool
) -> None:
    assert ProcessIdentity(_PID, _STARTED_AT).matches(live) is expected


def test_a_group_recorded_without_its_leader_matches_no_live_process() -> None:
    assert not ProcessIdentity(_PID, None).matches(ProcessIdentity(_PID, _STARTED_AT))
