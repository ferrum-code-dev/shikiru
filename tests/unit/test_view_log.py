from __future__ import annotations

from shikiru.view_log import turn_iteration


def test_turn_iteration_reads_the_iteration_a_turn_dir_was_started_in() -> None:
    cases = (
        ("02-implement-i1", 1),
        ("12-integrate-i12", 12),
        ("03-verify-i2", 2),
        ("04-integrate", None),
        ("05-review-i", None),
        ("06-review-ix", None),
    )
    for turn_name, iteration in cases:
        assert turn_iteration(turn_name) == iteration
