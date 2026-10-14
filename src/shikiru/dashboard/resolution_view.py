"""Renderables of the escalation resolution screen: title, Decision, Choose, choices, evidence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, auto
from typing import TYPE_CHECKING

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from shikiru.domain.escalation_panel import ChoiceKind
from shikiru.ui.section import (
    BOLD_STEEL,
    GROUP_SEPARATOR,
    Row,
    Section,
    grey,
    labelled_rows,
    row_name,
)
from shikiru.ui.tokens import (
    DUSTY_ROSE,
    SAGE,
    SECONDARY_TEXT_COLOR,
    STEEL_BLUE,
    STYLE_BOLD,
)

if TYPE_CHECKING:
    from rich.console import Console, ConsoleOptions, RenderResult

    from shikiru.domain.escalation_panel import PanelChoice, ResolutionPanel

NARROW_MAX_COLUMNS = 48
FOLDED_MAX_COLUMNS = 40
TINY_MAX_ROWS = 8
_LABEL_WIDTH = 9
_MARK_WIDTH = 3
_RECOMMENDED_STAR = "\N{BLACK STAR}"
_COLLAPSED_ARROW = "\N{BLACK RIGHT-POINTING SMALL TRIANGLE}"
_EXPANDED_ARROW = "\N{BLACK DOWN-POINTING SMALL TRIANGLE}"
_OWN_ANSWER_LABEL = "Write your own answer\N{HORIZONTAL ELLIPSIS}"
_ACCEPT_LABEL = "Accept"
_RETRY_LABEL = "Retry"
_CANCEL_LABEL = "Cancel task"
_CANCEL_CONFIRM_LABEL = "Press Enter again to cancel"
_DECISION_TITLE = "Decision"
_CHOOSE_TITLE = "Choose"
_SELECT_HINT = "\N{UPWARDS ARROW}\N{DOWNWARDS ARROW} select"
_SEND_HINT = "Enter send"
_EXPAND_HINT = "Enter expand"
_COLLAPSE_HINT = "Enter collapse"
_ANSWER_HINT = "Enter answer"
_DETAILS_HINT = "Tab add details"
_NEEDS_INPUT_MARK = "\N{LOWER RIGHT PENCIL}"
_NEEDS_INPUT_TEXT = "needs your input"
_TRIED_TITLE = "What was tried"
_WAITING_WORD = "waiting"
_TITLE_WIDE = "Resolve escalation"
_TITLE_NARROW = "Resolve"
_LABEL_TASK = "Task"
_LABEL_QUESTION = "Question"
_LABEL_REASON = "Reason"

_LABEL_STYLES = {
    ChoiceKind.ANSWER_OPTION: STEEL_BLUE,
    ChoiceKind.OWN_ANSWER: STEEL_BLUE,
    ChoiceKind.RETRY: STEEL_BLUE,
    ChoiceKind.ACCEPT: SAGE,
    ChoiceKind.CANCEL: DUSTY_ROSE,
}
_FIXED_LABELS = {
    ChoiceKind.OWN_ANSWER: _OWN_ANSWER_LABEL,
    ChoiceKind.ACCEPT: _ACCEPT_LABEL,
    ChoiceKind.RETRY: _RETRY_LABEL,
    ChoiceKind.CANCEL: _CANCEL_LABEL,
}


class ResolutionLayout(StrEnum):
    """How much of the panel a pane size leaves room for."""

    WIDE = auto()
    NARROW = auto()
    FOLDED = auto()
    TINY = auto()


_FOLDED_LAYOUTS = frozenset({ResolutionLayout.FOLDED, ResolutionLayout.TINY})


def layout_for(width: int, height: int) -> ResolutionLayout:
    """Pick the layout for a screen of `width` by `height` cells."""
    if height <= TINY_MAX_ROWS:
        return ResolutionLayout.TINY
    if width <= FOLDED_MAX_COLUMNS:
        return ResolutionLayout.FOLDED
    if width <= NARROW_MAX_COLUMNS:
        return ResolutionLayout.NARROW
    return ResolutionLayout.WIDE


def resolution_title(
    escalation_id: str, panel: ResolutionPanel | None, layout: ResolutionLayout
) -> str:
    """Return the popup title: ids, with task and reason folded in when the pane is small."""
    if panel is None:
        return GROUP_SEPARATOR.join((_TITLE_NARROW, escalation_id))
    if layout in _FOLDED_LAYOUTS:
        return GROUP_SEPARATOR.join((panel.task_id, panel.reason_label))
    head = _TITLE_WIDE if layout is ResolutionLayout.WIDE else _TITLE_NARROW
    return GROUP_SEPARATOR.join((head, escalation_id, panel.task_id))


@dataclass(frozen=True)
class _DecisionRows:
    panel: ResolutionPanel
    folded: bool

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        del console
        panel = self.panel
        if self.folded:
            yield row_name(panel.question) if panel.question else grey(panel.cause or "")
            return
        rows: list[Row] = [
            (grey(_LABEL_TASK), (row_name(panel.task_id), Text(panel.task_title))),
        ]
        if panel.question:
            rows.append((grey(_LABEL_QUESTION), (row_name(panel.question),)))
        if panel.cause or not panel.question:
            reason = Text()
            reason.append_text(row_name(panel.reason_label))
            if panel.cause:
                reason.append("\n")
                reason.append(panel.cause, style=SECONDARY_TEXT_COLOR)
            rows.append((grey(_LABEL_REASON), (reason,)))
        yield labelled_rows(rows, options.max_width, _LABEL_WIDTH)


def decision_renderable(
    panel: ResolutionPanel, age: str, layout: ResolutionLayout
) -> RenderableType:
    """Return the Decision section, or only its question when the pane is folded."""
    folded = layout in _FOLDED_LAYOUTS
    rows = _DecisionRows(panel, folded)
    if folded:
        return rows
    return Section(_DECISION_TITLE, rows, grey(f"{_WAITING_WORD} {age}"), ruled=False)


def _enter_hints(selected: PanelChoice | None, *, tried_expanded: bool) -> tuple[str, ...]:
    if selected is None:
        return (_COLLAPSE_HINT if tried_expanded else _EXPAND_HINT,)
    if selected.kind is not ChoiceKind.ANSWER_OPTION:
        return (_SEND_HINT,)
    return (_ANSWER_HINT,) if selected.needs_input else (_SEND_HINT, _DETAILS_HINT)


def choose_renderable(selected: PanelChoice | None, *, tried_expanded: bool) -> RenderableType:
    """Return the Choose heading with its key hint; `selected` is `None` on `What was tried`."""
    hint = GROUP_SEPARATOR.join(
        (_SELECT_HINT, *_enter_hints(selected, tried_expanded=tried_expanded))
    )
    return Section(_CHOOSE_TITLE, Group(), grey(hint), title_style=BOLD_STEEL)


def tried_renderable(content: str, *, expanded: bool) -> RenderableType:
    """Return the collapsible `What was tried` section."""
    arrow = _EXPANDED_ARROW if expanded else _COLLAPSED_ARROW
    body = grey(content) if expanded else Group()
    return Section(f"{_TRIED_TITLE} {arrow}", body)


def starred_indexes(panel: ResolutionPanel) -> frozenset[int]:
    """Return the rows to star: recommended ones, the own-answer row only when it stands alone."""
    has_alternatives = any(c.kind is ChoiceKind.ANSWER_OPTION for c in panel.choices)
    return frozenset(
        index
        for index, choice in enumerate(panel.choices)
        if choice.recommended and not (has_alternatives and choice.kind is ChoiceKind.OWN_ANSWER)
    )


def choice_renderable(
    choice: PanelChoice, *, starred: bool, armed: bool, disabled: bool
) -> RenderableType:
    """Return one choice row: star beside the label, its consequence beneath in grey."""
    style = SECONDARY_TEXT_COLOR if disabled else _LABEL_STYLES[choice.kind]
    if armed:
        label = Text(_CANCEL_CONFIRM_LABEL, style=f"{STYLE_BOLD} {DUSTY_ROSE}")
    else:
        label = Text(choice.label or _FIXED_LABELS[choice.kind], style=style)
        if choice.needs_input:
            label.append(f" {_NEEDS_INPUT_MARK}")
    star = _RECOMMENDED_STAR if starred else " "
    grid = Table.grid()
    grid.add_column(width=_MARK_WIDTH, no_wrap=True)
    grid.add_column(ratio=1)
    grid.add_row(Text(f" {star} ", style=style), label)
    if choice.kind is not ChoiceKind.OWN_ANSWER:
        consequence = (
            GROUP_SEPARATOR.join((_NEEDS_INPUT_TEXT, choice.consequence))
            if choice.needs_input
            else choice.consequence
        )
        grid.add_row(Text(" " * _MARK_WIDTH), grey(consequence))
    return grid
