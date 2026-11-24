"""The package smoke types a ladder once, and types it again only when the first typing was lost."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.process import ProcessResult
from tests.support import release_wait

if TYPE_CHECKING:
    from collections.abc import Sequence

    from _pytest.monkeypatch import MonkeyPatch

TMUX = "tmux"
SOCKET = "shikiru-smoke-test"
TARGET = "shikiru:0.0"
SCOPE = "Implementer · Base"
VALUE = "fake:strong"
CONFIRM_SECONDS_FOR_TESTS = 0.2
SLOW_APPLY_CAPTURES = 25
EDITOR_PAGE = "│  {scope}     {shown}  │\n│  Edit {scope}  │\n│  Ladder entries   │   │\n"


class FakeLadderEditor:
    def __init__(self, captures_before_applied: int, lost_typings: int = 0) -> None:
        self.captures_before_applied = captures_before_applied
        self.lost_typings = lost_typings
        self.typings = 0
        self.captures_since_typing: int | None = None

    async def run(self, command: Sequence[str], timeout: float) -> ProcessResult:
        del timeout
        if "capture-pane" in command:
            return ProcessResult(tuple(command), 0, self._page(), "")
        if "-l" in command and command[-1] == VALUE:
            self.typings += 1
            if self.lost_typings:
                self.lost_typings -= 1
            else:
                self.captures_since_typing = 0
        return ProcessResult(tuple(command), 0, "", "")

    def _page(self) -> str:
        applied = (
            self.captures_since_typing is not None
            and self.captures_since_typing >= self.captures_before_applied
        )
        if self.captures_since_typing is not None:
            self.captures_since_typing += 1
        return EDITOR_PAGE.format(scope=SCOPE, shown=VALUE if applied else "(empty)")


def _install(monkeypatch: MonkeyPatch, editor: FakeLadderEditor) -> None:
    monkeypatch.setattr(release_wait, "RUNNER", editor)
    monkeypatch.setattr(release_wait, "CLICK_CONFIRM_SECONDS", CONFIRM_SECONDS_FOR_TESTS)


async def test_a_ladder_the_app_applies_slowly_is_typed_once(monkeypatch: MonkeyPatch) -> None:
    editor = FakeLadderEditor(captures_before_applied=SLOW_APPLY_CAPTURES)
    _install(monkeypatch, editor)

    await release_wait.set_ladder(TMUX, SOCKET, TARGET, SCOPE, VALUE)

    assert editor.typings == 1


async def test_a_lost_ladder_typing_is_typed_again(monkeypatch: MonkeyPatch) -> None:
    editor = FakeLadderEditor(captures_before_applied=0, lost_typings=1)
    _install(monkeypatch, editor)

    await release_wait.set_ladder(TMUX, SOCKET, TARGET, SCOPE, VALUE)

    assert editor.typings == 2
