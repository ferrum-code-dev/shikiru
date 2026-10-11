from __future__ import annotations

import pytest

from shikiru.ui.recent_window import RecentWindow, StepTarget, step_selection

_SIZE = 3


def _window(offset: int, total: int) -> RecentWindow:
    return RecentWindow(offset, _SIZE, total)


@pytest.mark.parametrize(
    ("window", "shown", "paged", "later", "earlier"),
    [
        pytest.param(_window(0, 0), 0, False, False, False, id="empty"),
        pytest.param(_window(0, 3), 3, False, False, False, id="exactly-one-window"),
        pytest.param(_window(0, 7), 3, True, False, True, id="newest-page"),
        pytest.param(_window(3, 7), 3, True, True, True, id="middle-page"),
        pytest.param(_window(6, 7), 1, True, True, False, id="short-oldest-page"),
    ],
)
def test_window_reports_its_page_and_boundaries(
    window: RecentWindow,
    shown: int,
    paged: bool,
    later: bool,
    earlier: bool,
) -> None:
    assert (window.shown, window.paged, window.has_later, window.has_earlier) == (
        shown,
        paged,
        later,
        earlier,
    )


@pytest.mark.parametrize(
    ("window", "expected_offset"),
    [
        pytest.param(_window(0, 7), 0, id="in-range"),
        pytest.param(_window(9, 7), 6, id="past-the-end-moves-to-the-last-page"),
        pytest.param(_window(3, 0), 0, id="empty-resets"),
    ],
)
def test_window_clamps_the_offset_to_a_page_that_exists(
    window: RecentWindow, expected_offset: int
) -> None:
    assert window.clamped().offset == expected_offset


@pytest.mark.parametrize(
    ("position", "expected_offset"),
    [(0, 0), (2, 0), (3, 3), (5, 3), (6, 6)],
)
def test_window_page_of_a_position_starts_at_a_multiple_of_the_size(
    position: int, expected_offset: int
) -> None:
    assert _window(0, 7).at_page_of(position).offset == expected_offset


@pytest.mark.parametrize(
    ("active_count", "window", "current", "step", "expected"),
    [
        pytest.param(2, _window(0, 7), 0, 1, StepTarget(visible_index=1), id="down-in-active"),
        pytest.param(
            2, _window(0, 7), 1, 1, StepTarget(visible_index=2), id="down-from-last-active"
        ),
        pytest.param(
            2, _window(3, 7), 1, 1, StepTarget(visible_index=2), id="entering-recent-never-pages"
        ),
        pytest.param(
            2, _window(0, 7), 3, 1, StepTarget(visible_index=4), id="down-inside-the-window"
        ),
        pytest.param(
            2, _window(0, 7), 4, 1, StepTarget(recent_position=3), id="down-from-last-row-pages"
        ),
        pytest.param(2, _window(3, 7), 4, 1, StepTarget(recent_position=6), id="down-pages-again"),
        pytest.param(
            2, _window(6, 7), 2, 1, StepTarget(visible_index=0), id="down-from-oldest-wraps"
        ),
        pytest.param(
            2, _window(3, 7), 2, -1, StepTarget(recent_position=2), id="up-from-first-row-pages"
        ),
        pytest.param(
            2, _window(0, 7), 2, -1, StepTarget(visible_index=1), id="up-from-newest-goes-active"
        ),
        pytest.param(
            2, _window(3, 7), 0, -1, StepTarget(visible_index=4), id="up-from-first-active-wraps"
        ),
        pytest.param(
            2, _window(6, 7), 0, -1, StepTarget(visible_index=2), id="wrap-lands-on-shown-row"
        ),
        pytest.param(
            0, _window(6, 7), 0, 1, StepTarget(recent_position=0), id="only-recent-wraps-to-newest"
        ),
        pytest.param(
            0,
            _window(0, 7),
            0,
            -1,
            StepTarget(recent_position=6),
            id="only-recent-up-wraps-to-oldest",
        ),
        pytest.param(2, _window(0, 3), 4, 1, StepTarget(visible_index=0), id="single-window-wraps"),
        pytest.param(2, _window(0, 0), 1, 1, StepTarget(visible_index=0), id="no-recent"),
        pytest.param(
            2, _window(0, 7), None, 1, StepTarget(visible_index=0), id="no-selection-down"
        ),
        pytest.param(2, _window(0, 7), None, -1, StepTarget(visible_index=4), id="no-selection-up"),
    ],
)
def test_stepping_walks_active_then_recent_paging_at_the_window_edges(
    active_count: int,
    window: RecentWindow,
    current: int | None,
    step: int,
    expected: StepTarget,
) -> None:
    assert step_selection(active_count, window, current, step) == expected
