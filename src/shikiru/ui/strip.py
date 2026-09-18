"""The task strip renderable (docs/design/06-operator.md §6.2)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, auto

from rich.cells import cell_len
from rich.text import Text

from shikiru.domain.tasks import Mode, Role
from shikiru.ui.glyphs import (
    GLYPH_DONE,
    GLYPH_ESCALATED,
    GLYPH_OPERATOR,
    GLYPH_PAUSED,
    GLYPH_PENDING,
    GLYPH_QUOTA,
    GLYPH_REWORK,
    GLYPH_STALLED,
    PANEL_KANJI,
    ROLE_GLYPHS,
    STAGE_IMPLEMENT,
    STAGE_MERGE,
    STAGE_PLAN,
    STAGE_PR,
    STAGE_TRIAGE,
    STAGE_VERIFY,
)
from shikiru.ui.tokens import (
    ALERT_COLOR,
    ENGINE_COLOR,
    IDLE_COLOR,
    REWORK_COLOR,
    ROLE_COLORS,
    STYLE_BOLD,
    STYLE_DIM,
    STYLE_REVERSE,
    SUCCESS_COLOR,
)

STRIP_WIDE_MIN_COLUMNS = 60


class PanelState(StrEnum):
    """One panel's progress (06 §6.2)."""

    PENDING = auto()
    ACTIVE = auto()
    DONE = auto()
    REWORK = auto()
    ESCALATED = auto()


class StampKind(StrEnum):
    """A hold drawn over the active panel (06 §6.2)."""

    PAUSED = auto()
    OPERATOR = auto()
    QUOTA = auto()
    STALLED = auto()


_STATE_GLYPHS: dict[PanelState, str] = {
    PanelState.PENDING: GLYPH_PENDING,
    PanelState.DONE: GLYPH_DONE,
    PanelState.REWORK: GLYPH_REWORK,
    PanelState.ESCALATED: GLYPH_ESCALATED,
}
_STATE_STYLES: dict[PanelState, str] = {
    PanelState.PENDING: f"{STYLE_DIM} {IDLE_COLOR}",
    PanelState.DONE: f"{STYLE_BOLD} {SUCCESS_COLOR}",
    PanelState.REWORK: f"{STYLE_BOLD} {REWORK_COLOR}",
    PanelState.ESCALATED: f"{STYLE_REVERSE} {ALERT_COLOR}",
}
_STAMP_GLYPHS: dict[StampKind, str] = {
    StampKind.PAUSED: GLYPH_PAUSED,
    StampKind.OPERATOR: GLYPH_OPERATOR,
    StampKind.QUOTA: GLYPH_QUOTA,
    StampKind.STALLED: GLYPH_STALLED,
}
_STAMP_WORDS: dict[StampKind, str] = {
    StampKind.PAUSED: "PAUSED",
    StampKind.OPERATOR: "OPERATOR",
    StampKind.QUOTA: "QUOTA",
    StampKind.STALLED: "STALLED",
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


def panel_specs_for_mode(mode: Mode, *, pull_request: bool = False) -> tuple[tuple[str, str], ...]:
    """Return this mode's `(label, stage glyph)` panels (06 §6.2): implement, plan, or PR."""
    specs = _MODE_PANEL_LABELS[mode]
    if mode is Mode.IMPLEMENT and pull_request:
        specs = (*specs[:3], ("PR", STAGE_PR))
    return specs


@dataclass(frozen=True)
class PanelView:
    """One panel of a task strip (06 §6.2).

    `role` is the acting role, shown as the panel colour while `state` is `ACTIVE`; other
    states carry a fixed colour regardless of role. `rework_count` annotates a panel that
    reached its current state after one or more rejected iterations (e.g. `✔ ↺1 41m`).
    """

    label: str
    state: PanelState
    stage_glyph: str = ""
    detail: str = ""
    role_line: str = ""
    spinner_frame: str = ""
    role: Role | None = None
    rework_count: int = 0


@dataclass(frozen=True)
class StripStamp:
    """A hold drawn over the strip's active panel (06 §6.2)."""

    kind: StampKind
    detail: str = ""

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
    split_children: tuple[str, ...] = ()


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
        rework = f"{GLYPH_REWORK}{panel.rework_count}" if panel.rework_count else ""
        pieces = (_STATE_GLYPHS[panel.state], rework, panel.detail)
    return " ".join(piece for piece in pieces if piece)


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


def _column_widths(width: int, count: int) -> list[int]:
    available = width - (count + 1)
    base = available // count
    widths = [base] * count
    widths[-1] += available - base * count
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
        if self._data.split_children:
            return self._render_split(width)
        if width >= STRIP_WIDE_MIN_COLUMNS:
            return self._render_wide(width)
        return self._render_narrow(width)

    def _header_text(self) -> str:
        return f"{self._data.task_id}{_HEADER_SEPARATOR}{self._data.title}"

    def _render_split(self, width: int) -> Text:
        children = ", ".join(self._data.split_children)
        line = f"{self._data.task_id}{_HEADER_SEPARATOR}{self._data.title} → split → {children}"
        return Text(_fit(line, width) if width >= STRIP_WIDE_MIN_COLUMNS else line[:width])

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
        if cell_len(line) > width:
            text.truncate(width, overflow="crop")
        return text

    def _render_wide(self, width: int) -> Text:
        panels = self._data.panels
        widths = _column_widths(width, len(panels))

        header_cells = [
            _fit(_header_cell(index, panel), column)
            for index, (panel, column) in enumerate(zip(panels, widths, strict=True))
        ]
        body_cells = [
            _fit(_body_cell(panel), column) for panel, column in zip(panels, widths, strict=True)
        ]
        role_cells = [
            _fit(panel.role_line, column) for panel, column in zip(panels, widths, strict=True)
        ]

        stamp_index = _active_index(panels) if self._data.stamp is not None else None
        if stamp_index is not None and self._data.stamp is not None:
            body_cells[stamp_index] = _fit(self._data.stamp.render(), widths[stamp_index])
            role_cells[stamp_index] = _fit("", widths[stamp_index])

        body = Text(_top_border(self._header_text(), width), style=STYLE_BOLD)
        for row in (header_cells, body_cells, role_cells):
            body.append("\n")
            body.append(_BORDER_VERTICAL)
            for index, cell in enumerate(row):
                style = _panel_style(panels[index]) if row is body_cells else None
                body.append(cell, style=style)
                body.append(_BORDER_VERTICAL)
        body.append("\n")
        body.append(_bottom_border(widths))
        return body
