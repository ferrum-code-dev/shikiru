"""The Extra details popup: the escalation packet's evidence as one scrolling page."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from rich.cells import cell_len
from rich.console import Group, RenderableType
from rich.text import Text
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Rule, Static

from shikiru.dashboard.resolution_view import ResolutionLayout, layout_for
from shikiru.domain.escalation_evidence import EscalationPacket, parse_escalation_packet
from shikiru.ui.popup import (
    POPUP_BODY_CLASS,
    POPUP_CLOSE_CLASS,
    POPUP_CSS,
    POPUP_FRAME_CLASS,
    POPUP_HEADER_CLASS,
    POPUP_RULE_CLASS,
    POPUP_TITLE_CLASS,
    PopupFrame,
    popup_close_label,
    prepare_popup,
)
from shikiru.ui.section import BOLD_STEEL, GROUP_SEPARATOR, Row, Section, grey, labelled_rows
from shikiru.ui.tokens import AMBER, PRIMARY_TEXT_COLOR

if TYPE_CHECKING:
    from collections.abc import Callable

    from rich.console import Console, ConsoleOptions, RenderResult
    from textual import events
    from textual.app import ComposeResult

EXTRA_DETAILS_CLOSE_BUTTON = "extra-details-close"
_TITLE = "Extra details"
_ENTRY_PREFIX = "- "
_NONE_RECORDED = "(none)"
_TIME_FORMAT = "%m-%d %H:%M"
_MAX_LABEL_WIDTH = 14
_REPORT_LABEL_OPEN = "**"
_REPORT_SEPARATOR = "\n\n"
_SESSION_SEPARATOR = ": "
_ERROR_PREFIX = "Evidence error: "
_QUESTION_TITLE = "Question"
_TIMELINE = "Timeline"
_REPORTS = "Reports"
_FACTS = "Facts"
_RECENT_SIGNALS = "Recent turn signals"
_SESSIONS = "Sessions"
_SIGNALS_TITLE = "Recent signals"


def _value(text: str) -> tuple[Text, ...]:
    return (Text(text, style=PRIMARY_TEXT_COLOR),)


def _timeline_row(line: str) -> Row:
    entry = line.removeprefix(_ENTRY_PREFIX)
    stamp, _, message = entry.partition(" ")
    try:
        label = datetime.fromisoformat(stamp).strftime(_TIME_FORMAT)
    except ValueError:
        return _plain_row(entry)
    return grey(label), _value(message)


def _plain_row(line: str) -> Row:
    return Text(), _value(line)


def _report_rows(content: str) -> list[Row]:
    rows: list[Row] = []
    for block in content.split(_REPORT_SEPARATOR + _REPORT_LABEL_OPEN):
        label, _, body = block.removeprefix(_REPORT_LABEL_OPEN).partition(
            _REPORT_LABEL_OPEN + _REPORT_SEPARATOR
        )
        rows.append((grey(label), _value(body)) if body else _plain_row(block))
    return rows


def _session_row(line: str) -> Row:
    role, separator, rest = line.removeprefix(_ENTRY_PREFIX).partition(_SESSION_SEPARATOR)
    return (grey(role), _value(rest)) if separator else _plain_row(line)


def _line_rows(content: str, make: Callable[[str], Row]) -> list[Row]:
    return [make(line) for line in content.splitlines() if line.strip()]


_SECTIONS: dict[str, tuple[str, Callable[[str], list[Row]]]] = {
    _TIMELINE: (_TIMELINE, lambda content: _line_rows(content, _timeline_row)),
    _REPORTS: (_REPORTS, _report_rows),
    _FACTS: (_FACTS, lambda content: _line_rows(content, _plain_row)),
    _RECENT_SIGNALS: (_SIGNALS_TITLE, lambda content: _line_rows(content, _plain_row)),
    _SESSIONS: (_SESSIONS, lambda content: _line_rows(content, _session_row)),
}


@dataclass(frozen=True)
class _Rows:
    rows: list[Row]

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        del console
        label_width = min(
            max((cell_len(label.plain) for label, _ in self.rows), default=0), _MAX_LABEL_WIDTH
        )
        if label_width:
            yield labelled_rows(self.rows, options.max_width, label_width)
        else:
            yield Group(*(values[0] for _, values in self.rows))


def _titled(title: str, body: RenderableType) -> Section:
    return Section(title, body, ruled=False, title_style=BOLD_STEEL)


def _packet_sections(packet: EscalationPacket) -> list[RenderableType]:
    request = packet.agent_request
    question = request.question if request is not None else packet.question
    sections: list[RenderableType] = []
    if question:
        sections.append(_titled(_QUESTION_TITLE, Text(question, style=PRIMARY_TEXT_COLOR)))
    for label, content in packet.evidence_sections:
        if label not in _SECTIONS or not content or content == _NONE_RECORDED:
            continue
        title, make_rows = _SECTIONS[label]
        sections.append(_titled(title, _Rows(make_rows(content))))
    return sections


def extra_details_renderable(packet_text: str) -> RenderableType:
    """Return the sectioned page, or the amber parse error above the original packet text."""
    packet = parse_escalation_packet(packet_text)
    if packet.errors:
        error = Text(_ERROR_PREFIX + GROUP_SEPARATOR.join(packet.errors), style=AMBER)
        return Group(error, Text(""), Text(packet_text, style=PRIMARY_TEXT_COLOR))
    sections: list[RenderableType] = []
    for section in _packet_sections(packet):
        sections.extend((section, Text("")))
    return Group(*sections[:-1])


class ExtraDetailsScreen(ModalScreen[None]):
    """Show the evidence of one escalation until the Operator closes it with Esc."""

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [("escape", "close", "Close")]
    DEFAULT_CSS = (
        POPUP_CSS
        + """
    #extra-details-frame.compact { margin: 0 1; padding: 0 1; }
    #extra-details-title { text-wrap: nowrap; text-overflow: ellipsis; }
    #extra-details-scroll { max-height: 1fr; min-height: 0; }
    #extra-details-body { height: auto; }
    """
    )

    def __init__(self, task_id: str, packet_text: str) -> None:
        """Hold the task id for the title and the packet text to lay out."""
        super().__init__()
        prepare_popup(self)
        self._task_id = task_id
        self._packet_text = packet_text

    def compose(self) -> ComposeResult:
        """Lay out the popup frame with its header, rule and scrolling body."""
        with PopupFrame(widget_id="extra-details-frame", classes=POPUP_FRAME_CLASS):
            with Horizontal(classes=POPUP_HEADER_CLASS):
                yield Label(
                    GROUP_SEPARATOR.join((_TITLE, self._task_id)),
                    id="extra-details-title",
                    classes=POPUP_TITLE_CLASS,
                )
                yield Button(
                    popup_close_label(),
                    id=EXTRA_DETAILS_CLOSE_BUTTON,
                    classes=POPUP_CLOSE_CLASS,
                )
            yield Rule(classes=POPUP_RULE_CLASS)
            with VerticalScroll(id="extra-details-scroll", classes=POPUP_BODY_CLASS):
                yield Static(extra_details_renderable(self._packet_text), id="extra-details-body")

    def on_mount(self) -> None:
        self._fit()
        self.set_focus(self.query_one("#extra-details-scroll", VerticalScroll))
        self.call_after_refresh(self.query_one("#extra-details-frame", PopupFrame).content_ready)

    def on_resize(self, event: events.Resize) -> None:
        del event
        if self.is_mounted:
            self._fit()
            self.call_after_refresh(
                self.query_one("#extra-details-frame", PopupFrame).update_preferred_size
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == EXTRA_DETAILS_CLOSE_BUTTON:
            self.dismiss()

    def action_close(self) -> None:
        """Close the popup and return to the Escalations screen."""
        self.dismiss()

    def _fit(self) -> None:
        layout = layout_for(self.size.width, self.size.height)
        self.query_one("#extra-details-frame", PopupFrame).set_class(
            layout is not ResolutionLayout.WIDE, "compact"
        )
