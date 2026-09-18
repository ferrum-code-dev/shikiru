"""The task strip renderable (docs/design/06-operator.md §6.2)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, auto

from rich.cells import cell_len
from rich.text import Text

from shikiru.domain.tasks import Mode
from shikiru.ui.glyphs import (
    GLYPH_DONE,
    GLYPH_ESCALATED,
    GLYPH_OPERATOR,
    GLYPH_PAUSED,
    GLYPH_PENDING,
    GLYPH_QUOTA,
    GLYPH_REWORK,
    GLYPH_STALLED,
    STAGE_IMPLEMENT,
    STAGE_MERGE,
    STAGE_PLAN,
    STAGE_PR,
    STAGE_TRIAGE,
    STAGE_VERIFY,
)
from shikiru.ui.tokens import ALERT_COLOR, HOLD_COLOR, IDLE_COLOR, SUCCESS_COLOR

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
_STATE_WORDS: dict[PanelState, str] = {
    PanelState.PENDING: "pending",
    PanelState.ACTIVE: "active",
    PanelState.DONE: "done",
    PanelState.REWORK: "rework",
    PanelState.ESCALATED: "escalated",
}
_STATE_COLORS: dict[PanelState, str] = {
    PanelState.PENDING: IDLE_COLOR,
    PanelState.ACTIVE: SUCCESS_COLOR,
    PanelState.DONE: SUCCESS_COLOR,
    PanelState.REWORK: HOLD_COLOR,
    PanelState.ESCALATED: ALERT_COLOR,
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

_HEADER_SEPARATOR = " · "
_COLUMN_SEPARATOR = "|"

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
    """One panel of a task strip (06 §6.2)."""

    label: str
    state: PanelState
    stage_glyph: str = ""
    detail: str = ""
    role_line: str = ""
    spinner_frame: str = ""


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


def _state_cell(panel: PanelView) -> str:
    glyph = (
        panel.spinner_frame or "○"
        if panel.state is PanelState.ACTIVE
        else _STATE_GLYPHS[panel.state]
    )
    prefix = f"{glyph} {_STATE_WORDS[panel.state]}"
    return f"{prefix} {panel.detail}".rstrip() if panel.detail else prefix


def _label_cell(panel: PanelView) -> str:
    return f"{panel.stage_glyph} {panel.label}".strip()


def _active_or_last(panels: tuple[PanelView, ...]) -> PanelView:
    for panel in panels:
        if panel.state is PanelState.ACTIVE:
            return panel
    for panel in reversed(panels):
        if panel.state is not PanelState.PENDING:
            return panel
    return panels[0]


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

    def _header(self) -> str:
        header = f"{self._data.task_id} · {self._data.title}"
        if self._data.stamp is not None:
            header = f"{header}{_HEADER_SEPARATOR}{self._data.stamp.render()}"
        return header

    def _render_split(self, width: int) -> Text:
        children = ", ".join(self._data.split_children)
        line = f"{self._data.task_id} · {self._data.title} → split → {children}"
        return Text(_fit(line, width) if width >= STRIP_WIDE_MIN_COLUMNS else line[:width])

    def _render_narrow(self, width: int) -> Text:
        panel = _active_or_last(self._data.panels)
        pieces = [
            self._data.task_id,
            panel.stage_glyph,
            panel.label,
            _state_cell(panel),
            panel.role_line,
        ]
        line = " ".join(piece for piece in pieces if piece)
        if self._data.stamp is not None:
            line = f"{line}{_HEADER_SEPARATOR}{self._data.stamp.render()}"
        text = Text(line)
        if cell_len(line) > width:
            text.truncate(width, overflow="crop")
        return text

    def _render_wide(self, width: int) -> Text:
        panels = self._data.panels
        count = len(panels)
        available = width - (count - 1)
        base_width = available // count
        widths = [base_width] * count
        widths[-1] += available - base_width * count

        header = Text(_fit(self._header(), width), style="bold")
        label_cells = [
            _fit(_label_cell(panel), column) for panel, column in zip(panels, widths, strict=True)
        ]
        state_cells = [
            _fit(_state_cell(panel), column) for panel, column in zip(panels, widths, strict=True)
        ]
        role_cells = [
            _fit(panel.role_line, column) for panel, column in zip(panels, widths, strict=True)
        ]

        body = Text()
        body.append(header)
        for row in (label_cells, state_cells, role_cells):
            body.append("\n")
            for index, cell in enumerate(row):
                if index:
                    body.append(_COLUMN_SEPARATOR)
                style = _STATE_COLORS[panels[index].state] if row is state_cells else None
                body.append(cell, style=style)
        return body
