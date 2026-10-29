"""Body of the Usage fold: a totals line, one aligned line per session, one note."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from rich.cells import cell_len
from rich.console import Group
from rich.text import Text

from shikiru.agents.contracts import BILLING_MODE_SUBSCRIPTION, BILLING_MODE_UNKNOWN
from shikiru.dashboard.briefing_view import counted, total_cost_text
from shikiru.domain.levels import UsageLevel, displayed_pct
from shikiru.ui.compact import (
    COUNT_SHORT_MILLION,
    COUNT_SHORT_SCALE,
    ROLE_LABELS,
    compact_count,
)
from shikiru.ui.section import BOLD_STEEL, GROUP_SEPARATOR, grey
from shikiru.ui.tokens import AMBER, SECONDARY_TEXT_COLOR, USAGE_LEVEL_COLORS

if TYPE_CHECKING:
    from collections.abc import Sequence

    from rich.console import Console, ConsoleOptions, RenderableType, RenderResult

    from shikiru.dashboard.contract import TaskDetailView, TaskSessionView

NO_VALUE = "\N{EM DASH}"
NO_USAGE = "No usage is recorded yet."
COVERAGE_UNAVAILABLE = "Task usage coverage unavailable"
OLD_TAG = "old"
TOKENS_WORD = " tokens"
NOTE_LEAD = "! "
LEAD = "  "
GAP = "  "
ELLIPSIS = "\N{HORIZONTAL ELLIPSIS}"
MODEL_MIN_WIDTH = 8
SIGNIFICANT_DIGITS = 3
PAIR_SEPARATOR = " / "
PAIR_LABEL = "r/w"
_NOUN_SESSION = "session"
_NOUN_GAP = "model-history gap"
_VERB_NO_USAGE = "reported no usage"
_PHRASE_BILLING_UNKNOWN = "billing unknown for"


class Column(StrEnum):
    """The session table columns, in display order, named as their header shows them."""

    ROLE = "Role"
    MODEL = "Model"
    TOKENS = "Tokens r/w"
    COST = "Cost"
    CONTEXT = "Ctx"


_LEFT_ALIGNED = (Column.ROLE, Column.MODEL)
_DROP_ORDER = (Column.MODEL, Column.CONTEXT, Column.COST)


@dataclass(frozen=True)
class UsageRow:
    """One session's cell texts, its context level and whether it is an older session."""

    cells: dict[Column, str]
    level: UsageLevel | None
    old: bool


@dataclass(frozen=True)
class UsageTable:
    """The columns that fit the width, their widths, the rows and whether they stack."""

    columns: tuple[Column, ...]
    widths: dict[Column, int]
    rows: tuple[UsageRow, ...]
    stacked: bool


def usage_rows(sessions: Sequence[TaskSessionView], width: int) -> UsageTable:
    """Derive the session table for `width`: shrink the model, drop it, then drop Ctx and Cost."""
    ordered = sorted(sessions, key=lambda session: not session.current_lineage)
    rows = tuple(_row(session) for session in ordered)
    columns = list(Column)
    widths = {
        column: max(cell_len(column.value), *(cell_len(row.cells[column]) for row in rows))
        for column in columns
    }
    old_room = (len(GAP) + len(OLD_TAG)) if any(row.old for row in rows) else 0

    def total() -> int:
        gaps = len(GAP) * (len(columns) - 1)
        return len(LEAD) + sum(widths[c] for c in columns) + gaps + old_room

    overflow = total() - width
    if overflow > 0:
        widths[Column.MODEL] = max(MODEL_MIN_WIDTH, widths[Column.MODEL] - overflow)
    for column in _DROP_ORDER:
        if total() > width:
            columns.remove(column)
    return UsageTable(tuple(columns), widths, rows, stacked=total() > width)


def _row(session: TaskSessionView) -> UsageRow:
    return UsageRow(
        {
            Column.ROLE: ROLE_LABELS.get(session.role, session.role.title()),
            Column.MODEL: session.model,
            Column.TOKENS: _tokens(session),
            Column.COST: _cost(session),
            Column.CONTEXT: _context(session),
        },
        session.context_level,
        not session.current_lineage,
    )


def _tokens_known(session: TaskSessionView) -> bool:
    return session.usage_reported is True or session.legacy_token_totals


def _session_tokens(session: TaskSessionView) -> int:
    return session.input_tokens + session.output_tokens


def _tokens(session: TaskSessionView) -> str:
    if not _tokens_known(session):
        return NO_VALUE
    return PAIR_SEPARATOR.join(
        (_abbreviated(session.input_tokens), _abbreviated(session.output_tokens))
    )


def _abbreviated(count: int) -> str:
    if count < COUNT_SHORT_SCALE:
        return str(count)
    thousands = _significant(Decimal(count) / COUNT_SHORT_SCALE)
    if thousands < COUNT_SHORT_SCALE:
        return f"{_plain(thousands)}k"
    return f"{_plain(_significant(Decimal(count) / COUNT_SHORT_MILLION))}M"


def _significant(value: Decimal) -> Decimal:
    exponent = value.adjusted() - (SIGNIFICANT_DIGITS - 1)
    return value.quantize(Decimal(1).scaleb(exponent), rounding=ROUND_HALF_UP)


def _plain(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _cost(session: TaskSessionView) -> str:
    if session.cost_usd is None or session.billing_mode == BILLING_MODE_SUBSCRIPTION:
        return NO_VALUE
    return f"${session.cost_usd:.2f}"


def _context(session: TaskSessionView) -> str:
    if session.usage_reported is False or session.context_used_pct is None:
        return NO_VALUE
    return f"{displayed_pct(session.context_used_pct)}%"


def _fit(text: str, width: int) -> str:
    if cell_len(text) <= width:
        return text
    return text[: width - len(ELLIPSIS)] + ELLIPSIS


def _padded(text: str, width: int, *, left: bool) -> str:
    fitted = _fit(text, width)
    padding = " " * (width - cell_len(fitted))
    return fitted + padding if left else padding + fitted


def _line(table: UsageTable, cells: dict[Column, str], style: str) -> Text:
    return Text(
        LEAD
        + GAP.join(
            _padded(cells[column], table.widths[column], left=column in _LEFT_ALIGNED)
            for column in table.columns
        ),
        style=style,
    )


def _row_text(table: UsageTable, row: UsageRow) -> Text:
    line = _line(table, row.cells, "")
    if row.level is not None and row.level is not UsageLevel.OK and Column.CONTEXT in table.columns:
        context_width = table.widths[Column.CONTEXT]
        line.stylize(USAGE_LEVEL_COLORS[row.level], len(line) - context_width, len(line))
    return _old_tagged(line, row)


def _old_tagged(line: Text, row: UsageRow) -> Text:
    if row.old:
        line.append(f"{GAP}{OLD_TAG}", style=SECONDARY_TEXT_COLOR)
    return line


def _table_lines(table: UsageTable) -> list[Text]:
    if table.stacked:
        return [line for row in table.rows for line in _stacked_row(row)]
    header = _line(table, {column: column.value for column in Column}, SECONDARY_TEXT_COLOR)
    return [header, *(_row_text(table, row) for row in table.rows)]


def _stacked_row(row: UsageRow) -> list[Text]:
    role = _old_tagged(Text(LEAD + row.cells[Column.ROLE]), row)
    pair = Text.assemble(LEAD, GAP, grey(PAIR_LABEL), GAP, row.cells[Column.TOKENS])
    return [role, pair]


def _heading(sessions: Sequence[TaskSessionView]) -> Text | None:
    parts: list[Text] = []
    cost = total_cost_text(sessions)
    if cost is not None:
        parts.append(Text(cost))
    known = [_session_tokens(session) for session in sessions if _tokens_known(session)]
    if known:
        parts.append(Text.assemble((compact_count(sum(known)), BOLD_STEEL), grey(TOKENS_WORD)))
    return Text(GROUP_SEPARATOR).join(parts) if parts else None


def _note(sessions: Sequence[TaskSessionView]) -> Text | None:
    silent = sum(session.usage_reported is False for session in sessions)
    gaps = sum(len(session.history_gaps) for session in sessions)
    unknown = sum(session.billing_mode == BILLING_MODE_UNKNOWN for session in sessions)
    parts: list[str] = []
    if silent:
        parts.append(f"{counted(silent, _NOUN_SESSION)} {_VERB_NO_USAGE}")
    if gaps:
        parts.append(counted(gaps, _NOUN_GAP))
    if unknown:
        parts.append(f"{_PHRASE_BILLING_UNKNOWN} {counted(unknown, _NOUN_SESSION)}")
    return Text(NOTE_LEAD + GROUP_SEPARATOR.join(parts), style=AMBER) if parts else None


@dataclass(frozen=True)
class UsageBody:
    """Render the Usage fold of a task at the width it is drawn at."""

    view: TaskDetailView

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        """Yield the heading, the session table and the note."""
        del console
        view = self.view
        if view.usage is None:
            yield grey(COVERAGE_UNAVAILABLE)
        if not view.sessions:
            if view.usage is not None:
                yield grey(NO_USAGE)
            return
        table = usage_rows(view.sessions, options.max_width)
        heading = _heading(view.sessions)
        blocks: list[RenderableType] = [
            *([heading] if heading is not None else []),
            *_table_lines(table),
        ]
        note = _note(view.sessions)
        if note is not None:
            blocks.extend([Text(), note])
        yield Group(*blocks)
