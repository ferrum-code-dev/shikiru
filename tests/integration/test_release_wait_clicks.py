"""The package smoke's button click is sent again when lost, and never after it has landed."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from shikiru.process import ProcessResult
from tests.support import release_wait

if TYPE_CHECKING:
    from collections.abc import Sequence

    from _pytest.monkeypatch import MonkeyPatch

TMUX = "tmux"
SOCKET = "shikiru-smoke-test"
TARGET = "shikiru:0.0"
NEXT_LABEL = "Next"
LADDERS_PAGE = "SHIKIRU · Ladders\n\n  [ Back ]  [ Cancel ]  [ Next ]\n"
TRACKERS_PAGE = "SHIKIRU · Trackers\n\n  [ Back ]  [ Cancel ]  [ Finish ]\n"
TRACKERS_HEADING = "SHIKIRU · Trackers"
CONFIRM_SECONDS_FOR_TESTS = 0.05


class FakeWizard:
    def __init__(self, page: str, lost_clicks: int = 0) -> None:
        self.page = page
        self.lost_clicks = lost_clicks
        self.presses = 0

    async def run(self, command: Sequence[str], timeout: float) -> ProcessResult:
        del timeout
        if "capture-pane" in command:
            return ProcessResult(tuple(command), 0, self.page, "")
        if command[-1].endswith(release_wait.MOUSE_PRESS_FINAL):
            self._press()
        return ProcessResult(tuple(command), 0, "", "")

    def _press(self) -> None:
        self.presses += 1
        if self.lost_clicks:
            self.lost_clicks -= 1
        elif self.page == LADDERS_PAGE:
            self.page = TRACKERS_PAGE


def _install(monkeypatch: MonkeyPatch, wizard: FakeWizard) -> None:
    monkeypatch.setattr(release_wait, "RUNNER", wizard)
    monkeypatch.setattr(release_wait, "CLICK_CONFIRM_SECONDS", CONFIRM_SECONDS_FOR_TESTS)


async def _click_next() -> None:
    await release_wait.click_button(TMUX, SOCKET, TARGET, NEXT_LABEL, TRACKERS_HEADING)


async def test_a_click_that_lands_is_sent_once(monkeypatch: MonkeyPatch) -> None:
    wizard = FakeWizard(LADDERS_PAGE)
    _install(monkeypatch, wizard)

    await _click_next()

    assert wizard.page == TRACKERS_PAGE
    assert wizard.presses == 1


async def test_a_lost_click_is_sent_again_until_it_lands(monkeypatch: MonkeyPatch) -> None:
    wizard = FakeWizard(LADDERS_PAGE, lost_clicks=1)
    _install(monkeypatch, wizard)

    await _click_next()

    assert wizard.page == TRACKERS_PAGE
    assert wizard.presses == 2


async def test_a_button_already_gone_is_not_clicked(monkeypatch: MonkeyPatch) -> None:
    wizard = FakeWizard(TRACKERS_PAGE)
    _install(monkeypatch, wizard)

    await _click_next()

    assert wizard.presses == 0


async def test_every_lost_click_fails_with_the_last_capture(monkeypatch: MonkeyPatch) -> None:
    wizard = FakeWizard(LADDERS_PAGE, lost_clicks=release_wait.CLICK_ATTEMPTS)
    _install(monkeypatch, wizard)

    with pytest.raises(RuntimeError, match="did not reach") as failure:
        await _click_next()

    assert wizard.presses == release_wait.CLICK_ATTEMPTS
    assert LADDERS_PAGE in str(failure.value)
