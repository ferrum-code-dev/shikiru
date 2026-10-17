"""The Help popup: one tab per context, opened on the context it was asked from."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from rich.cells import cell_len
from rich.text import Text
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Rule, Static

from shikiru.dashboard.help_keys import HelpAvailability, HelpGroup, HelpTab, help_groups
from shikiru.ui.keys import key_hint
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
from shikiru.ui.section import BOLD_STEEL, Section, grey, labelled_rows
from shikiru.ui.tokens import STEEL_BLUE

if TYPE_CHECKING:
    from rich.console import Console, ConsoleOptions, RenderResult
    from textual import events
    from textual.app import ComposeResult
    from textual.binding import BindingType

HELP_CLOSE_BUTTON = "workspace-help-close"
HELP_CONTEXT = "workspace-help-context"
HELP_TABS = "workspace-help-tabs"
HELP_HINT = "workspace-help-hint"
HELP_TITLE = "Help"
HELP_SCROLL = "workspace-help-scroll"
HELP_COPY = "workspace-help-copy"
HELP_FRAME = "workspace-help-frame"
COMPACT_MAX_COLUMNS = 48
COMPACT_MAX_ROWS = 20
TINY_MAX_ROWS = 10
_SWITCH_ARROWS = "\N{LEFTWARDS ARROW} \N{RIGHTWARDS ARROW}"
_SWITCH_LABEL = " switch section"
TAB_CLASS = "help-tab"
TAB_ACTIVE_CLASS = "-active"
SWITCH_ACTION = "switch"
SELECT_ACTION = "select_tab"
_TABS = tuple(HelpTab)


@dataclass(frozen=True)
class HelpContext:
    """What Help was opened from: its tab and the Home parts that exist."""

    opening: HelpTab
    availability: HelpAvailability


class _TabLabel(Static):
    def __init__(self, index: int, tab: HelpTab) -> None:
        super().__init__(tab.value, classes=TAB_CLASS, markup=False)
        self._index = index

    async def on_click(self) -> None:
        await self.run_action(f"screen.{SELECT_ACTION}({self._index})")


class _KeyTable:
    def __init__(self, groups: tuple[HelpGroup, ...]) -> None:
        self._groups = groups

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        del console
        for index, group in enumerate(self._groups):
            if index:
                yield Text("")
            label_width = max(cell_len(row.keys) for row in group.rows)
            rows = [
                (Text(row.keys, style=BOLD_STEEL), (key_hint(row.action),)) for row in group.rows
            ]
            body = labelled_rows(rows, options.max_width, label_width)
            yield Section(group.title, body, ruled=False, title_style=STEEL_BLUE)


class _SectionHeightTable:
    def __init__(self, active: _KeyTable, sections: tuple[_KeyTable, ...]) -> None:
        self._active = active
        self._sections = sections

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        heights = [
            len(console.render_lines(section, options, pad=False)) for section in self._sections
        ]
        shown = len(console.render_lines(self._active, options, pad=False))
        yield self._active
        for _ in range(max(heights) - shown):
            yield Text("")


class WorkspaceHelpScreen(ModalScreen[None]):
    """Help with a tab per context; Left and Right switch tabs and the body scrolls."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "Close"),
        Binding("left", f"{SWITCH_ACTION}(-1)", "Previous tab", show=False, priority=True),
        Binding("right", f"{SWITCH_ACTION}(1)", "Next tab", show=False, priority=True),
    ]
    DEFAULT_CSS = (
        POPUP_CSS
        + f"""
    #{HELP_TABS} {{ height: 1; overflow: hidden; }}
    #{HELP_HINT} {{ height: 1; margin-bottom: 1; }}
    .{TAB_CLASS} {{ width: auto; margin: 0 2 0 0; color: $muted-text; }}
    .{TAB_CLASS}.{TAB_ACTIVE_CLASS} {{
        color: $steel-blue; text-style: bold; background: $selected-surface;
    }}
    #{HELP_SCROLL} {{ max-height: 1fr; min-height: 0; }}
    #{HELP_COPY} {{ height: auto; color: $primary-text; }}
    #{HELP_FRAME}.compact {{ margin: 0 1; padding: 0 1; }}
    """
    )

    def __init__(self, context: HelpContext) -> None:
        super().__init__()
        prepare_popup(self)
        self._help = context
        self._tab = context.opening

    def compose(self) -> ComposeResult:
        """Render the header, the tab row and the key table of the opening tab."""
        with PopupFrame(widget_id=HELP_FRAME, classes=POPUP_FRAME_CLASS):
            with Horizontal(classes=POPUP_HEADER_CLASS):
                yield Label(HELP_TITLE, id=HELP_CONTEXT, classes=POPUP_TITLE_CLASS)
                yield Button(popup_close_label(), id=HELP_CLOSE_BUTTON, classes=POPUP_CLOSE_CLASS)
            yield Rule(classes=POPUP_RULE_CLASS)
            with Horizontal(id=HELP_TABS):
                for index, tab in enumerate(_TABS):
                    yield _TabLabel(index, tab)
            yield Static(self._hint(keys_only=False), id=HELP_HINT, markup=False)
            with VerticalScroll(id=HELP_SCROLL, classes=POPUP_BODY_CLASS):
                yield Static(self._table(), id=HELP_COPY, markup=False)

    def _hint(self, *, keys_only: bool) -> Text:
        hint = Text(_SWITCH_ARROWS, style=BOLD_STEEL)
        if not keys_only:
            hint.append_text(grey(_SWITCH_LABEL))
        return hint

    def on_mount(self) -> None:
        self._apply_compact_layout(self.size.width, self.size.height)
        self._paint_tabs()
        self.set_focus(self.query_one(f"#{HELP_SCROLL}", VerticalScroll))
        self.call_after_refresh(self.query_one(f"#{HELP_FRAME}", PopupFrame).content_ready)

    def on_resize(self, event: events.Resize) -> None:
        self._apply_compact_layout(event.size.width, event.size.height)
        frame = self.query_one(f"#{HELP_FRAME}", PopupFrame)
        frame.reset_preferred_size()
        self.call_after_refresh(frame.content_ready)

    def _apply_compact_layout(self, width: int, height: int) -> None:
        frame = self.query_one(f"#{HELP_FRAME}", PopupFrame)
        frame.set_class(width <= COMPACT_MAX_COLUMNS or height <= COMPACT_MAX_ROWS, "compact")
        self.query_one(f"#{HELP_HINT}", Static).update(
            self._hint(keys_only=height <= TINY_MAX_ROWS)
        )

    def _paint_tabs(self) -> None:
        for tab, label in zip(_TABS, self.query(f".{TAB_CLASS}"), strict=True):
            label.set_class(tab is self._tab, TAB_ACTIVE_CLASS)

    def _table(self) -> _SectionHeightTable:
        sections = tuple(_KeyTable(help_groups(tab, self._help.availability)) for tab in _TABS)
        return _SectionHeightTable(sections[_TABS.index(self._tab)], sections)

    def action_select_tab(self, index: int) -> None:
        """Show the tab at `index` from the top."""
        tab = _TABS[index]
        if tab is self._tab:
            return
        self._tab = tab
        self._paint_tabs()
        self.query_one(f"#{HELP_COPY}", Static).update(self._table())
        self.query_one(f"#{HELP_SCROLL}", VerticalScroll).scroll_home(animate=False)

    def action_switch(self, step: int) -> None:
        """Show the neighbouring tab, stopping at both ends."""
        self.action_select_tab(min(max(_TABS.index(self._tab) + step, 0), len(_TABS) - 1))

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Close help when its button is activated."""
        if event.button.id == HELP_CLOSE_BUTTON:
            self.dismiss()

    async def action_close(self) -> None:
        """Dismiss contextual help."""
        self.dismiss()
