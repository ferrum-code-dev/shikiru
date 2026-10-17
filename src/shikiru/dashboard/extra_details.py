"""The Extra details popup: the escalation packet's evidence as one scrolling page."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from rich.console import Group, RenderableType
from rich.text import Text
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Rule, Static

from shikiru.dashboard.evidence_table import EvidenceTable, LabelledRows
from shikiru.dashboard.resolution_view import ResolutionLayout, layout_for
from shikiru.dashboard.row_list import RowList
from shikiru.dashboard.section_rows import TONE_COLORS, glyph_prefix
from shikiru.domain.escalation_evidence import EscalationPacket, parse_escalation_packet
from shikiru.domain.packet_evidence import parse_reports, parse_signals
from shikiru.domain.timeline_phrases import NO_GLYPH, kind_phrase
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
from shikiru.ui.section import BOLD_STEEL, GROUP_SEPARATOR, Row, Section, grey
from shikiru.ui.tokens import AMBER, PRIMARY_TEXT_COLOR

if TYPE_CHECKING:
    from collections.abc import Callable

    from textual import events
    from textual.app import ComposeResult
    from textual.widget import Widget

EXTRA_DETAILS_CLOSE_BUTTON = "extra-details-close"
_TITLE = "Extra details"
_ENTRY_PREFIX = "- "
_NONE_RECORDED = "(none)"
_TIME_FORMAT = "%m-%d %H:%M"
_KIND_SEPARATOR = ": "
_RESUME_SEPARATOR = " \N{EM DASH} "
_SESSION_MARKER = " session `"
_SESSION_ID_CLOSE = "`"
_SESSION_ID_WIDTH = 8
_ERROR_PREFIX = "Evidence error: "
_QUESTION_TITLE = "Question"
_TIMELINE = "Timeline"
_REPORTS = "Reports"
_RECENT_SIGNALS = "Recent turn signals"
_SESSIONS = "Sessions"
_SIGNALS_TITLE = "Recent signals"
_REPORTS_TABLE = "extra-details-reports"
_SIGNALS_TABLE = "extra-details-signals"


def _value(text: str) -> tuple[Text, ...]:
    return (Text(text, style=PRIMARY_TEXT_COLOR),)


def _plain_row(line: str) -> Row:
    return Text(), _value(line)


def _timeline_row(line: str) -> Row:
    entry = line.removeprefix(_ENTRY_PREFIX)
    stamp, _, message = entry.partition(" ")
    try:
        label = datetime.fromisoformat(stamp).strftime(_TIME_FORMAT)
    except ValueError:
        return _plain_row(entry)
    kind, separator, detail = message.partition(_KIND_SEPARATOR)
    phrase = kind_phrase(kind)
    if phrase is None:
        return grey(label), (Text(glyph_prefix(NO_GLYPH) + message, style=PRIMARY_TEXT_COLOR),)
    text = glyph_prefix(phrase.glyph) + (phrase.text + separator + detail)
    return grey(label), (Text(text, style=TONE_COLORS[phrase.tone.value] or PRIMARY_TEXT_COLOR),)


def _session_row(line: str) -> Row:
    entry = line.removeprefix(_ENTRY_PREFIX).partition(_RESUME_SEPARATOR)[0]
    role, _, rest = entry.partition(_KIND_SEPARATOR)
    agent, marker, session = rest.partition(_SESSION_MARKER)
    if not marker:
        return _plain_row(entry)
    short = session.removesuffix(_SESSION_ID_CLOSE)[:_SESSION_ID_WIDTH]
    return grey(role), (*_value(agent), grey(short))


def _line_rows(content: str, make: Callable[[str], Row]) -> list[Row]:
    return [make(line) for line in content.splitlines() if line.strip()]


_ROW_SECTIONS: dict[str, tuple[str, Callable[[str], Row]]] = {
    _TIMELINE: (_TIMELINE, _timeline_row),
    _SESSIONS: (_SESSIONS, _session_row),
}


def _titled(title: str, body: RenderableType) -> Section:
    return Section(title, body, ruled=False, title_style=BOLD_STEEL)


def _static(title: str, rows: list[Row]) -> Static:
    return Static(_titled(title, LabelledRows(rows)))


def _page_widgets(packet: EscalationPacket) -> list[Widget]:
    request = packet.agent_request
    question = request.question if request is not None else packet.question
    widgets: list[Widget] = []
    if question:
        widgets.append(Static(_titled(_QUESTION_TITLE, Text(question, style=PRIMARY_TEXT_COLOR))))
    for label, content in packet.evidence_sections:
        if not content or content == _NONE_RECORDED:
            continue
        if label in _ROW_SECTIONS:
            title, make = _ROW_SECTIONS[label]
            widgets.append(_static(title, _line_rows(content, make)))
        elif label == _REPORTS and (reports := parse_reports(content)):
            widgets.append(EvidenceTable(_REPORTS, reports, table_id=_REPORTS_TABLE))
        elif label == _RECENT_SIGNALS and (signals := parse_signals(content)):
            widgets.append(EvidenceTable(_SIGNALS_TITLE, signals, table_id=_SIGNALS_TABLE))
    return widgets


def _error_widget(packet: EscalationPacket, packet_text: str) -> Static:
    error = Text(_ERROR_PREFIX + GROUP_SEPARATOR.join(packet.errors), style=AMBER)
    return Static(Group(error, Text(""), Text(packet_text, style=PRIMARY_TEXT_COLOR)))


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
    #extra-details-body > * { margin-top: 1; }
    #extra-details-body > :first-child { margin-top: 0; }
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
            with (
                VerticalScroll(id="extra-details-scroll", classes=POPUP_BODY_CLASS),
                Vertical(id="extra-details-body"),
            ):
                yield from self._page()

    def _page(self) -> list[Widget]:
        packet = parse_escalation_packet(self._packet_text)
        if packet.errors:
            return [_error_widget(packet, self._packet_text)]
        return _page_widgets(packet)

    def on_mount(self) -> None:
        self._fit()
        tables = self.query(RowList)
        first = tables.first() if tables else self.query_one("#extra-details-scroll")
        self.set_focus(first, scroll_visible=False)
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
