"""A status strip pinned above a scrolling terminal view, without touching the output below it."""

from __future__ import annotations

import os
import re
import select
import termios
import textwrap
import tty
from typing import TYPE_CHECKING

from rich.text import Text

if TYPE_CHECKING:
    from typing import TextIO

    from rich.console import Console

_ESCAPE = "\x1b"
_CSI = f"{_ESCAPE}["
_SAVE_CURSOR = f"{_ESCAPE}7"
_RESTORE_CURSOR = f"{_ESCAPE}8"
_RESET_REGION = f"{_CSI}r"
_ERASE_LINE = f"{_CSI}2K"
_CURSOR_REPORT_REQUEST = f"{_CSI}6n"
_CURSOR_REPORT = re.compile(rf"{_ESCAPE}\[(\d+);\d+R")
_CURSOR_REPORT_TIMEOUT_SECONDS = 2.0
_REPORT_READ_BYTES = 64
_FIRST_COLUMN = 1
_FIRST_ROW = 1
_NO_ROWS: tuple[str, ...] = ()
_CLEAR_SCROLLBACK = f"{_CSI}3J"
_CLEAR_SCREEN = f"{_CSI}2J"
_HOME = f"{_CSI}H"


class CursorReportError(RuntimeError):
    """The terminal did not report where its cursor is."""


def _go_to(row: int) -> str:
    return f"{_CSI}{row};{_FIRST_COLUMN}H"


class StatusStrip:
    """Reserves the top rows of a terminal for a status text and keeps the output below it.

    The strip sits in rows `1..height`; output scrolls in the rows beneath it through a scroll
    region, so it never overwrites or duplicates the strip. Showing or hiding the strip shifts
    the visible output by its height instead of overwriting it; the rows pushed off the top
    stay in the terminal's scrollback. A horizontal resize clears the reflowed terminal buffer;
    the view then restores its output from the event log under the newly sized strip.
    """

    def __init__(self, console: Console, keyboard: TextIO, style: str) -> None:
        """Draw onto `console` in `style`, asking the terminal behind `keyboard` for its cursor."""
        self._console = console
        self._keyboard = keyboard
        self._style = style
        self._rows: tuple[str, ...] = _NO_ROWS
        self._width: int = console.size.width
        self._height: int = console.size.height

    def sync(self, text: str | None) -> None:
        """Show `text` in the strip, hide the strip for none, and redraw after a terminal resize."""
        size = self._console.size
        rows = self._wrap(text, size.width, size.height)
        if rows == self._rows and size.width == self._width and size.height == self._height:
            return
        self._width = size.width
        self._height = size.height
        self._apply(rows, size.height)

    @property
    def active(self) -> bool:
        """Return whether a status currently reserves terminal rows."""
        return bool(self._rows)

    def reset_after_resize(self) -> None:
        """Clear reflowed screen and scrollback before the view restores its logged output."""
        out = self._console.file
        out.write(_CLEAR_SCREEN + _CLEAR_SCROLLBACK + _RESET_REGION + _HOME)
        out.flush()
        self._rows = _NO_ROWS
        self._width = self._console.size.width
        self._height = self._console.size.height

    def _wrap(self, text: str | None, width: int, height: int) -> tuple[str, ...]:
        if text is None:
            return _NO_ROWS
        return tuple(textwrap.wrap(text, width) or [""])[: max(height - 1, 1)]

    def _apply(self, rows: tuple[str, ...], terminal_height: int) -> None:
        previous = len(self._rows)
        row = min(self._cursor_row(), terminal_height)
        out = self._console.file
        out.write(_RESET_REGION)
        if len(rows) > previous:
            grown = len(rows) - previous
            overflow = max(row + grown - terminal_height, 0)
            out.write(_go_to(terminal_height) + "\n" * overflow)
            out.write(_go_to(previous + 1) + f"{_CSI}{grown}L")
            row = min(row + grown, terminal_height)
        elif len(rows) < previous:
            shrunk = previous - len(rows)
            out.write(_go_to(len(rows) + 1) + f"{_CSI}{shrunk}M")
            row = max(row - shrunk, _FIRST_ROW)
        if rows:
            out.write(f"{_CSI}{len(rows) + 1};{terminal_height}r")
        out.write(_go_to(row))
        out.flush()
        self._rows = rows
        self._draw(rows)

    def _draw(self, rows: tuple[str, ...]) -> None:
        out = self._console.file
        out.write(_SAVE_CURSOR)
        for index, line in enumerate(rows):
            out.write(_go_to(index + 1) + _ERASE_LINE)
            self._console.print(Text(line, style=self._style), end="")
        out.write(_RESTORE_CURSOR)
        out.flush()

    def _cursor_row(self) -> int:
        descriptor = self._keyboard.fileno()
        saved = termios.tcgetattr(descriptor)
        try:
            tty.setcbreak(descriptor)
            self._console.file.write(_CURSOR_REPORT_REQUEST)
            self._console.file.flush()
            received = ""
            while True:
                ready, _, _ = select.select([descriptor], [], [], _CURSOR_REPORT_TIMEOUT_SECONDS)
                if not ready:
                    message = "the terminal did not answer the cursor position request"
                    raise CursorReportError(message)
                received += self._read(descriptor)
                report = _CURSOR_REPORT.search(received)
                if report is not None:
                    return int(report.group(1))
        finally:
            termios.tcsetattr(descriptor, termios.TCSADRAIN, saved)

    def _read(self, descriptor: int) -> str:
        return os.read(descriptor, _REPORT_READ_BYTES).decode(errors="ignore")
