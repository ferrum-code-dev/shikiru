"""A role view replays its whole log once after the terminal or the quota strip changes size."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import io
import json
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

from shikiru.cli.view import ViewTail
from shikiru.domain.quota_waits import QuotaAction
from shikiru.domain.tasks import Role
from shikiru.ui.status_strip import StatusStrip
from shikiru.ui.stream import StreamLine
from shikiru.ui.tokens import AMBER
from shikiru.view_log import write_quota_status
from tests.support.clock import FakeClock
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path
    from typing import TextIO

_START = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
_ROWS = 40
_TURN = "01-implement"
_RUNG = {"agent": "beta", "model": "impl-1", "effort": None}
_WIDTH = 80
_HEIGHT = 20
_SHORT_HEIGHT = 12
_NARROW_WIDTH = 20
_STEP_SECONDS = 1.0
_TIMEOUT_SECONDS = 5.0
_CURSOR_REQUEST = "\x1b[6n"
_CURSOR_REPLY = b"\x1b[1;1R"
_CLEAR_SCROLLBACK = "\x1b[3J"


def _marker(index: int) -> str:
    return f"M{index:03}"


def _row(text: str) -> str:
    return json.dumps({"ts": "t", "turn": _TURN, "rung": _RUNG, "signal": "Message", "text": text})


class _Terminal(io.StringIO):
    """Recording output that answers each cursor-position request on the keyboard pty."""

    def __init__(self, keyboard_master: int) -> None:
        super().__init__()
        self._master = keyboard_master

    def write(self, text: str) -> int:
        if _CURSOR_REQUEST in text:
            os.write(self._master, _CURSOR_REPLY)
        return super().write(text)


@dataclasses.dataclass(frozen=True)
class _Pty:
    master: int
    keyboard: TextIO


@pytest.fixture
def pty() -> Iterator[_Pty]:
    master, slave = os.openpty()
    keyboard = os.fdopen(slave, "r", closefd=False)
    try:
        yield _Pty(master, keyboard)
    finally:
        keyboard.close()
        os.close(slave)
        os.close(master)


@dataclasses.dataclass
class _Scene:
    console: Console
    status_path: Path
    clock: FakeClock


def _narrow(scene: _Scene) -> None:
    scene.console.size = (_WIDTH // 2, _HEIGHT)


def _shorten(scene: _Scene) -> None:
    scene.console.size = (_WIDTH, _SHORT_HEIGHT)


def _publish_a_longer_status(scene: _Scene) -> None:
    write_quota_status(scene.status_path, QuotaAction.RETRY, scene.clock.now())


@pytest.mark.parametrize(
    ("width", "change"),
    [
        pytest.param(_WIDTH, _narrow, id="width-resize"),
        pytest.param(_WIDTH, _shorten, id="height-resize"),
        pytest.param(_NARROW_WIDTH, _publish_a_longer_status, id="status-growth"),
    ],
)
async def test_a_change_replays_every_logged_row_once_after_the_strip_is_redrawn(
    pty: _Pty,
    tmp_path: Path,
    width: int,
    change: Callable[[_Scene], None],
) -> None:
    log = tmp_path / "view.jsonl"
    log.write_text("".join(_row(_marker(index)) + "\n" for index in range(_ROWS)))
    status_path = tmp_path / "quota-status.json"
    write_quota_status(status_path, None, None)
    output = _Terminal(pty.master)
    console = Console(file=output, width=width, height=_HEIGHT)
    clock = FakeClock(_START)
    tail = ViewTail(
        log,
        StreamLine(Role.IMPLEMENTER),
        console,
        clock,
        status_path=status_path,
        strip=StatusStrip(console, pty.keyboard, AMBER),
    )
    follower = asyncio.create_task(tail.follow())
    try:
        await wait_until(clock.has_pending_sleep, timeout=_TIMEOUT_SECONDS)
        change(_Scene(console, status_path, clock))
        clock.advance(_STEP_SECONDS)
        await wait_until(clock.has_pending_sleep, timeout=_TIMEOUT_SECONDS)
    finally:
        follower.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await follower

    _, clear, replayed = output.getvalue().rpartition(_CLEAR_SCROLLBACK)
    assert clear, "the view must clear its output before replaying it"
    counts = {_marker(index): replayed.count(_marker(index)) for index in range(_ROWS)}
    assert all(count == 1 for count in counts.values()), counts
