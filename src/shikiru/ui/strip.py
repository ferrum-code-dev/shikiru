"""The task strip renderable (docs/design/06-operator.md §6.2)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, auto
from typing import TYPE_CHECKING

from rich.cells import cell_len
from rich.text import Text

from shikiru.domain.tasks import Mode, Role
from shikiru.ui.glyphs import (
    GLYPH_DONE,
    GLYPH_ESCALATED,
    GLYPH_OPERATOR,
    GLYPH_PAUSED,
    GLYPH_PENDING,
    GLYPH_STALLED,
    PANEL_KANJI,
    ROLE_GLYPHS,
    STAGE_IMPLEMENT,
    STAGE_MERGE,
    STAGE_PLAN,
    STAGE_TRIAGE,
    STAGE_VERIFY,
)
from shikiru.ui.tokens import (
    ALERT_COLOR,
    ENGINE_COLOR,
    HOLD_COLOR,
    IDLE_COLOR,
    ROLE_COLORS,
    STYLE_BOLD,
    STYLE_DIM,
    STYLE_REVERSE,
    SUCCESS_COLOR,
)

if TYPE_CHECKING:
    from datetime import datetime

STRIP_WIDE_MIN_COLUMNS = 60


class PanelState(StrEnum):
    """One panel's progress (06 §6.2)."""

    PENDING = auto()
    ACTIVE = auto()
    DONE = auto()
    ESCALATED = auto()
    STALLED = auto()


class StampKind(StrEnum):
    """A hold drawn over the active panel (06 §6.2)."""

    PAUSED = auto()
    OPERATOR = auto()
    VERDICT = auto()
    MERGED = auto()


_STATE_GLYPHS: dict[PanelState, str] = {
    PanelState.PENDING: GLYPH_PENDING,
    PanelState.DONE: GLYPH_DONE,
    PanelState.ESCALATED: GLYPH_ESCALATED,
    PanelState.STALLED: GLYPH_STALLED,
}
_STATE_STYLES: dict[PanelState, str] = {
    PanelState.PENDING: f"{STYLE_DIM} {IDLE_COLOR}",
    PanelState.DONE: f"{STYLE_BOLD} {SUCCESS_COLOR}",
    PanelState.ESCALATED: f"{STYLE_REVERSE} {ALERT_COLOR}",
    PanelState.STALLED: f"{STYLE_BOLD} {HOLD_COLOR}",
}
_STAMP_GLYPHS: dict[StampKind, str] = {
    StampKind.PAUSED: GLYPH_PAUSED,
    StampKind.OPERATOR: GLYPH_OPERATOR,
    StampKind.VERDICT: GLYPH_DONE,
    StampKind.MERGED: GLYPH_DONE,
}
_STAMP_WORDS: dict[StampKind, str] = {
    StampKind.PAUSED: "PAUSED",
    StampKind.OPERATOR: "OPERATOR",
    StampKind.VERDICT: "VERDICT",
    StampKind.MERGED: "MERGED",
}

_NARROW_SEPARATOR = " "
_HEADER_SEPARATOR = " · "

_BORDER_TOP_LEFT = "┏"
_BORDER_TOP_RIGHT = "┓"
_BORDER_BOTTOM_LEFT = "┗"
_BORDER_BOTTOM_RIGHT = "┛"
_BORDER_BOTTOM_JOIN = "┻"
_BORDER_HORIZONTAL = "━"
_BORDER_VERTICAL = "┃"
_TOP_BORDER_PREFIX = f"{_BORDER_TOP_LEFT}{_BORDER_HORIZONTAL} "

_MODE_PANEL_LABELS: dict[Mode, tuple[tuple[str, str], ...]] = {
    Mode.IMPLEMENT: (
        ("Triage", STAGE_TRIAGE),
        ("Implement", STAGE_IMPLEMENT),
        ("Verify", STAGE_VERIFY),
        ("Merge", STAGE_MERGE),
    ),
    Mode.PLAN: (
        ("Triage", STAGE_TRIAGE),
        ("Plan", STAGE_PLAN),
        ("—", ""),
        ("Merge", STAGE_MERGE),
    ),
}


def panel_specs_for_mode(mode: Mode) -> tuple[tuple[str, str], ...]:
    """Return this mode's `(label, stage glyph)` panels (06 §6.2)."""
    return _MODE_PANEL_LABELS[mode]


@dataclass(frozen=True)
class PanelView:
    """One panel of a task strip (06 §6.2).

    `role` is the acting role, shown as the panel colour while `state` is `ACTIVE`; other
    states carry a fixed colour regardless of role.
    """

    label: str
    state: PanelState
    stage_glyph: str = ""
    detail: str = ""
    role_line: str = ""
    spinner_frame: str = ""
    role: Role | None = None


@dataclass(frozen=True)
class StripStamp:
    """A hold drawn over the strip's active panel (06 §6.2)."""

    kind: StampKind
    detail: str = ""
    flash: bool = False

    def render(self) -> str:
        """Render as `<glyph> <WORD>`, with `detail` appended when set (e.g. a reset time)."""
        text = f"{_STAMP_GLYPHS[self.kind]} {_STAMP_WORDS[self.kind]}"
        return f"{text} · {self.detail}" if self.detail else text


@dataclass(frozen=True)
class TaskStripData:
    """Everything `TaskStrip` needs to render one task, already resolved by its caller."""

    task_id: str
    title: str
    panels: tuple[PanelView, ...] = ()
    stamp: StripStamp | None = None
    actual_step_started_at: datetime | None = None
    last_signal_at: datetime | None = None
    last_signal: str | None = None
    last_words: str | None = None


def _fit(text: str, width: int) -> str:
    if cell_len(text) <= width:
        return text + " " * (width - cell_len(text))
    truncated = Text(text)
    truncated.truncate(width, overflow="crop")
    return truncated.plain


def _panel_style(panel: PanelView) -> str:
    if panel.state is PanelState.ACTIVE:
        color = ROLE_COLORS[panel.role] if panel.role is not None else ENGINE_COLOR
        return f"{STYLE_BOLD} {color}"
    return _STATE_STYLES[panel.state]


def _narrow_glyph(panel: PanelView) -> str:
    if panel.state is PanelState.ACTIVE:
        return panel.spinner_frame or GLYPH_PENDING
    return _STATE_GLYPHS[panel.state]


def _header_cell(index: int, panel: PanelView) -> str:
    pieces = (PANEL_KANJI[index], panel.stage_glyph, panel.label)
    return " ".join(piece for piece in pieces if piece)


def _body_cell(panel: PanelView) -> str:
    if panel.state is PanelState.ACTIVE:
        role_glyph = ROLE_GLYPHS[panel.role] if panel.role is not None else ""
        pieces = (panel.spinner_frame or GLYPH_PENDING, role_glyph, panel.detail)
    else:
        pieces = (_STATE_GLYPHS[panel.state], panel.detail)
    return " ".join(piece for piece in pieces if piece)


def _stamp_index(panels: tuple[PanelView, ...]) -> int | None:
    active = _active_index(panels)
    if active is not None:
        return active
    for index in range(len(panels) - 1, -1, -1):
        if panels[index].state is not PanelState.PENDING:
            return index
    return 0 if panels else None


def _active_or_last(panels: tuple[PanelView, ...]) -> PanelView:
    for panel in panels:
        if panel.state is PanelState.ACTIVE:
            return panel
    for panel in reversed(panels):
        if panel.state is not PanelState.PENDING:
            return panel
    return panels[0]


def _active_index(panels: tuple[PanelView, ...]) -> int | None:
    for index, panel in enumerate(panels):
        if panel.state is PanelState.ACTIVE:
            return index
    return None


def _column_widths(width: int, headers: list[str]) -> list[int]:
    count = len(headers)
    available = width - (count + 1)
    minimums = [cell_len(header) for header in headers]
    if sum(minimums) > available:
        base = available // count
        widths = [base] * count
        widths[-1] += available - base * count
        return widths
    widths = list(minimums)
    widths[-1] += available - sum(minimums)
    return widths


def _top_border(header: str, width: int) -> str:
    fixed = cell_len(_TOP_BORDER_PREFIX) + 1 + cell_len(_BORDER_TOP_RIGHT)
    header = _fit(header, max(width - fixed, 0)).rstrip()
    fill = width - cell_len(_TOP_BORDER_PREFIX) - cell_len(header) - 1 - cell_len(_BORDER_TOP_RIGHT)
    return f"{_TOP_BORDER_PREFIX}{header} {_BORDER_HORIZONTAL * max(fill, 0)}{_BORDER_TOP_RIGHT}"


def _bottom_border(widths: list[int]) -> str:
    segments = (_BORDER_HORIZONTAL * column for column in widths)
    return f"{_BORDER_BOTTOM_LEFT}{_BORDER_BOTTOM_JOIN.join(segments)}{_BORDER_BOTTOM_RIGHT}"


class TaskStrip:
    """Renders one task's strip at a given terminal width (06 §6.2)."""

    def __init__(self, data: TaskStripData) -> None:
        """Hold `data`; nothing here mutates it."""
        self._data = data

    def render(self, width: int) -> Text:
        """Render the full 4-panel strip at `width >= STRIP_WIDE_MIN_COLUMNS`, else one line."""
        if width >= STRIP_WIDE_MIN_COLUMNS:
            return self._render_wide(width)
        return self._render_narrow(width)

    def _header_text(self) -> str:
        return f"{self._data.task_id}{_HEADER_SEPARATOR}{self._data.title}"

    def _render_narrow(self, width: int) -> Text:
        panel = _active_or_last(self._data.panels)
        pieces = [
            self._data.task_id,
            panel.stage_glyph,
            panel.label,
            panel.detail,
            panel.role_line,
            _narrow_glyph(panel),
        ]
        line = _NARROW_SEPARATOR.join(piece for piece in pieces if piece)
        if self._data.stamp is not None:
            line = f"{line}{_HEADER_SEPARATOR}{self._data.stamp.render()}"
        text = Text(line)
        if self._data.stamp is not None and self._data.stamp.flash:
            stamp = self._data.stamp.render()
            start = line.rfind(stamp)
            if start >= 0:
                text.stylize(STYLE_REVERSE, start, start + cell_len(stamp))
        if cell_len(line) > width:
            text.truncate(width, overflow="crop")
        return text

    def _render_wide(self, width: int) -> Text:
        panels = self._data.panels
        headers = [_header_cell(index, panel) for index, panel in enumerate(panels)]
        widths = _column_widths(width, headers)

        header_cells = [
            _fit(header, column) for header, column in zip(headers, widths, strict=True)
        ]
        body_cells = [
            _fit(_body_cell(panel), column) for panel, column in zip(panels, widths, strict=True)
        ]
        role_cells = [
            _fit(panel.role_line, column) for panel, column in zip(panels, widths, strict=True)
        ]

        stamp_index = _stamp_index(panels) if self._data.stamp is not None else None
        if stamp_index is not None and self._data.stamp is not None:
            body_cells[stamp_index] = _fit(self._data.stamp.render(), widths[stamp_index])
            role_cells[stamp_index] = _fit("", widths[stamp_index])

        body = Text(_top_border(self._header_text(), width), style=STYLE_BOLD)
        for row in (header_cells, body_cells, role_cells):
            body.append("\n")
            body.append(_BORDER_VERTICAL)
            for index, cell in enumerate(row):
                style = _panel_style(panels[index]) if row is body_cells else None
                if (
                    row is body_cells
                    and index == stamp_index
                    and self._data.stamp is not None
                    and self._data.stamp.flash
                ):
                    style = f"{style} {STYLE_REVERSE}"
                body.append(cell, style=style)
                body.append(_BORDER_VERTICAL)
        body.append("\n")
        body.append(_bottom_border(widths))
        return body
