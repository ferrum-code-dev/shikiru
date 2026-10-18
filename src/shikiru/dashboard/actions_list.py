"""The grouped Actions list: choose what to do with a task or the project (06 §5.5)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import partial
from typing import TYPE_CHECKING, ClassVar, cast

from rich.cells import cell_len
from rich.text import Text
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Label, ListItem, ListView, Rule, Static

from shikiru.dashboard.contract import (
    ExtensionEntry,
    PaletteAction,
    PaletteRequest,
    PaletteReturn,
    TaskPaletteRoute,
    TaskRouteChoice,
)
from shikiru.dashboard.covered import Coverable
from shikiru.dashboard.palette import SUBMIT_ACTION_LABELS
from shikiru.dashboard.task_picker import TaskPickerScreen
from shikiru.domain.commands import ResolutionKind
from shikiru.domain.escalation import EscalationStatus
from shikiru.errors import ConfigError
from shikiru.operator.queries import (
    TASK_ACTION_OTHER_ROLE_TAKEN_OVER,
    TASK_ACTION_ROLES,
    OperatorTaskAction,
)
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
from shikiru.ui.section import GROUP_SEPARATOR, WrappedGroups, grey
from shikiru.ui.tokens import (
    PRIMARY_TEXT_COLOR,
    SECONDARY_TEXT_COLOR,
    STYLE_BOLD,
    SUCCESS_COLOR,
)

if TYPE_CHECKING:
    from rich.console import Console, ConsoleOptions, RenderResult
    from textual import events
    from textual.app import App, ComposeResult
    from textual.binding import BindingType
    from textual.screen import Screen
    from textual.timer import Timer
    from textual.worker import Worker

    from shikiru.dashboard.contract import OnChangeRegistration, ResolutionScreenFactory
    from shikiru.operator.queries import OperatorQueries

PopupFactory = Callable[[PaletteRequest], ModalScreen[str | None]]

ACTIONS_FRAME = "actions-frame"
ACTIONS_TITLE = "actions-title"
ACTIONS_CLOSE_BUTTON = "actions-close"
ACTIONS_LIST = "actions-list"
ACTIONS_HINT = "actions-hint"
ACTIONS_HEADING = "Actions"
PROJECT_CONTEXT = "Project"
TASK_GROUP = "Task"
VIEWS_GROUP = "Views"
EXTENSIONS_GROUP = "Extensions"
PROJECT_GROUP = "Project"
ELLIPSIS = "\N{HORIZONTAL ELLIPSIS}"
MORE_LABEL = f"More{ELLIPSIS}"
FEWER_LABEL = f"Fewer{ELLIPSIS}"
SWITCH_LABEL = f"Switch task{ELLIPSIS}"
ANSWER_LABEL = "Answer"
EVIDENCE_LABEL = "Evidence & reports"
USAGE_LABEL = "Usage"
COMMANDS_LABEL = "Commands"
DIAGNOSTICS_LABEL = "Project diagnostics"
ANSWER_ROW = "answer"
MORE_ROW = "more"
SWITCH_ROW = "switch-task"
ACTION_ROW_PREFIX = "action:"
ROUTE_ROW_PREFIX = "route:"
EXTENSION_ROW_PREFIX = "extension:"
MEANINGS: dict[str, str] = {
    ANSWER_ROW: "Answer the recorded decision",
    PaletteAction.MESSAGE: "Delivered on the next turn",
    PaletteAction.TAKE_OVER: "Stop the turn, work in the pane",
    PaletteAction.HAND_BACK: "Return this role to automation",
    PaletteAction.PAUSE: "Stop after the current turn",
    PaletteAction.RESUME: "Clear the task holds",
    PaletteAction.CANCEL: "Abandon this task",
    PaletteAction.PAUSE_NOW: "Stop the current turn, then hold",
    PaletteAction.PRIORITIZE: "Change the dispatch order",
    PaletteAction.RETRY: "Re-run the current step",
    PaletteAction.RUNG: "Set the level for the next turn",
    PaletteAction.PEEK: "Show recorded session activity",
    PaletteAction.EXPORT: "Write the task evidence to a file",
    PaletteAction.SUBMIT: "Create a new task",
    PaletteAction.STOP: "Stop running turns, keep the state",
    TaskPaletteRoute.EVIDENCE: "Reports, verdicts and engine facts",
    TaskPaletteRoute.USAGE: "Recorded session values",
    TaskPaletteRoute.COMMANDS: "Durable outcomes",
    TaskPaletteRoute.DIAGNOSTICS: "Secondary observations",
    SWITCH_ROW: "Act on another task",
    FEWER_LABEL: "Hide the extra actions",
}
ROUTE_LABELS = {
    TaskPaletteRoute.EVIDENCE: EVIDENCE_LABEL,
    TaskPaletteRoute.USAGE: USAGE_LABEL,
    TaskPaletteRoute.COMMANDS: COMMANDS_LABEL,
    TaskPaletteRoute.DIAGNOSTICS: DIAGNOSTICS_LABEL,
}
PRIMARY_TASK_ACTIONS = (
    PaletteAction.MESSAGE,
    PaletteAction.TAKE_OVER,
    PaletteAction.HAND_BACK,
    PaletteAction.PAUSE,
    PaletteAction.RESUME,
    PaletteAction.CANCEL,
)
EXTRA_TASK_ACTIONS = (
    PaletteAction.PAUSE_NOW,
    PaletteAction.PRIORITIZE,
    PaletteAction.RETRY,
    PaletteAction.RUNG,
    PaletteAction.PEEK,
    PaletteAction.EXPORT,
)
ROLE_ACTIONS = frozenset(
    {
        PaletteAction.TAKE_OVER,
        PaletteAction.HAND_BACK,
        PaletteAction.RUNG,
        PaletteAction.MESSAGE,
        PaletteAction.PEEK,
    }
)
TASK_LABEL_WIDTH = max(
    cell_len(label)
    for label in (
        ANSWER_LABEL,
        MORE_LABEL,
        FEWER_LABEL,
        *(SUBMIT_ACTION_LABELS[action] for action in (*PRIMARY_TASK_ACTIONS, *EXTRA_TASK_ACTIONS)),
    )
)
LABEL_GAP = "  "
ROW_INDENT = "  "
TITLE_INDENT = " "
CONFIRMATION_GLYPH = " \N{CHECK MARK}"
CONFIRMATION_STYLE = f"{STYLE_BOLD} {SUCCESS_COLOR}"
MIN_MEANING_CELLS = 8
MIN_TASK_TITLE_CELLS = 8
HINT_GAP = "   "
HINT_SELECT = "↑↓ select"
HINT_OPEN = "Enter open"
COMPACT_CLASS = "compact"
COMPACT_MAX_COLUMNS = 48
COMPACT_MAX_ROWS = 20
CLOSE_ACTION = "close"
MOVE_ACTION = "move"
JUMP_ACTION = "jump"
SYNC_WORKER = "actions-list-sync"
CHANGE_WORKER = "actions-list-changes"
RESOLUTION_NOT_CONFIGURED = "the escalation resolution screen is not configured"


@dataclass(frozen=True)
class ActionRow:
    """One selectable row: a stable `key`, its `label` and a short grey `meaning`."""

    key: str
    label: str
    meaning: str = ""


@dataclass(frozen=True)
class ActionGroup:
    """A titled group of rows whose labels share one column `label_width` wide."""

    title: str
    rows: tuple[ActionRow, ...]
    label_width: int


@dataclass(frozen=True)
class ListContext:
    """What the Actions list can offer now; `task_actions` is empty when there is no task."""

    has_task: bool
    task_actions: frozenset[PaletteAction]
    answerable: bool
    expanded: bool
    routes: bool
    diagnostics: bool
    extensions: tuple[ExtensionEntry, ...]


def _action_row(action: PaletteAction) -> ActionRow:
    return ActionRow(
        f"{ACTION_ROW_PREFIX}{action.value}", SUBMIT_ACTION_LABELS[action], MEANINGS[action]
    )


def _route_row(route: TaskPaletteRoute) -> ActionRow:
    return ActionRow(f"{ROUTE_ROW_PREFIX}{route.value}", ROUTE_LABELS[route], MEANINGS[route])


def _widest(rows: tuple[ActionRow, ...]) -> int:
    return max((cell_len(row.label) for row in rows), default=0)


def _task_group(context: ListContext) -> ActionGroup:
    rows: list[ActionRow] = []
    if context.has_task:
        if context.answerable:
            rows.append(ActionRow(ANSWER_ROW, ANSWER_LABEL, MEANINGS[ANSWER_ROW]))
        rows.extend(_action_row(a) for a in PRIMARY_TASK_ACTIONS if a in context.task_actions)
        extras = tuple(a for a in EXTRA_TASK_ACTIONS if a in context.task_actions)
        if extras:
            rows.append(_more_row(context.expanded, extras))
            if context.expanded:
                rows.extend(_action_row(action) for action in extras)
    return ActionGroup(TASK_GROUP, tuple(rows), TASK_LABEL_WIDTH)


def _more_row(expanded: bool, extras: tuple[PaletteAction, ...]) -> ActionRow:
    if expanded:
        return ActionRow(MORE_ROW, FEWER_LABEL, MEANINGS[FEWER_LABEL])
    return ActionRow(
        MORE_ROW,
        MORE_LABEL,
        GROUP_SEPARATOR.join(SUBMIT_ACTION_LABELS[action] for action in extras),
    )


def _views_group(context: ListContext) -> ActionGroup:
    routes = (TaskPaletteRoute.EVIDENCE, TaskPaletteRoute.USAGE, TaskPaletteRoute.COMMANDS)
    rows = tuple(_route_row(route) for route in routes if context.has_task and context.routes)
    if context.has_task and context.diagnostics:
        rows += (_route_row(TaskPaletteRoute.DIAGNOSTICS),)
    return ActionGroup(VIEWS_GROUP, rows, _widest(rows))


def _extensions_group(context: ListContext) -> ActionGroup:
    rows = tuple(
        ActionRow(f"{EXTENSION_ROW_PREFIX}{index}", extension.label)
        for index, extension in enumerate(context.extensions)
    )
    return ActionGroup(EXTENSIONS_GROUP, rows, _widest(rows))


def _project_group() -> ActionGroup:
    rows = (
        _action_row(PaletteAction.SUBMIT),
        _action_row(PaletteAction.STOP),
        ActionRow(SWITCH_ROW, SWITCH_LABEL, MEANINGS[SWITCH_ROW]),
    )
    return ActionGroup(PROJECT_GROUP, rows, _widest(rows))


def action_groups(context: ListContext) -> tuple[ActionGroup, ...]:
    """Return the non-empty groups of the Actions list: Task, Views, Extensions, Project."""
    groups = (
        _task_group(context),
        _views_group(context),
        _extensions_group(context),
        _project_group(),
    )
    return tuple(group for group in groups if group.rows)


def row_text(row: ActionRow, label_width: int, width: int, *, confirmed: bool = False) -> Text:
    """Return `label  meaning` on one line of `width` cells; the meaning shrinks or drops first."""
    label = Text(row.label, style=PRIMARY_TEXT_COLOR)
    if confirmed:
        label.append(CONFIRMATION_GLYPH, style=CONFIRMATION_STYLE)
    head = Text.assemble(ROW_INDENT, label)
    column = cell_len(ROW_INDENT) + label_width + cell_len(LABEL_GAP)
    room = width - column
    if not row.meaning or room < MIN_MEANING_CELLS:
        head.truncate(width, overflow="ellipsis")
        return Text.assemble(head, no_wrap=True, overflow="ellipsis")
    head.pad_right(column - cell_len(head.plain))
    meaning = grey(row.meaning)
    meaning.truncate(room, overflow="ellipsis")
    return Text.assemble(head, meaning, no_wrap=True, overflow="ellipsis")


def list_title(
    task_id: str | None, task_title: str, role: str | None, width: int | None = None
) -> str:
    """Return `Actions · T-n title · Role`, or `Actions · Project`; the title shrinks to fit."""
    if task_id is None:
        return f"{ACTIONS_HEADING}{GROUP_SEPARATOR}{PROJECT_CONTEXT}"
    suffix = f"{GROUP_SEPARATOR}{role.title()}" if role else ""
    head = f"{ACTIONS_HEADING}{GROUP_SEPARATOR}{task_id}"
    title = task_title.replace("\n", " ")
    room = None if not width else width - cell_len(head) - cell_len(suffix) - 1
    if room is not None and cell_len(title) > room:
        if room < MIN_TASK_TITLE_CELLS:
            return f"{head}{suffix}"
        shown = Text(title)
        shown.truncate(room, overflow="ellipsis")
        title = shown.plain
    return f"{head} {title}{suffix}"


class _RowText:
    def __init__(self, row: ActionRow, label_width: int, *, confirmed: bool) -> None:
        self._row = row
        self._label_width = label_width
        self._confirmed = confirmed

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        del console
        yield row_text(self._row, self._label_width, options.max_width, confirmed=self._confirmed)


class GroupTitleItem(ListItem):
    def __init__(self, title: str) -> None:
        super().__init__(Static(Text(f"{TITLE_INDENT}{title}"), markup=False), disabled=True)
        self.title = title


class ActionItem(ListItem):
    def __init__(self, row: ActionRow, label_width: int, *, confirmed: bool) -> None:
        self._text = Static(_RowText(row, label_width, confirmed=confirmed), markup=False)
        super().__init__(self._text)
        self.row = row

    def show(self, row: ActionRow, label_width: int, *, confirmed: bool) -> None:
        self.row = row
        self._text.update(_RowText(row, label_width, confirmed=confirmed))


class _TitleLabel(Label):
    def __init__(self, refit: Callable[[], None], *, widget_id: str) -> None:
        super().__init__(ACTIONS_HEADING, id=widget_id, classes=POPUP_TITLE_CLASS)
        self._refit = refit

    def on_resize(self) -> None:
        self._refit()


class _ActionList(ListView):
    def on_resize(self) -> None:
        self.call_after_refresh(self._reveal_selection)

    def watch_index(self, old_index: int | None, new_index: int | None) -> None:
        super().watch_index(old_index, new_index)
        self.call_after_refresh(self._reveal_selection)

    def _reveal_selection(self) -> None:
        item = self.highlighted_child
        if item is None or self.index is None:
            return
        above = self.index - 1
        heading = self._nodes[above] if above >= 0 else None
        (heading if isinstance(heading, GroupTitleItem) else item).scroll_visible(animate=False)


class ActionsListScreen(Coverable, ModalScreen[PaletteReturn | None]):
    """Show the grouped Actions list; rows open popups, views, extensions or another task."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", CLOSE_ACTION, "Close"),
        Binding("up", f"{MOVE_ACTION}(-1)", "Up", show=False, priority=True),
        Binding("down", f"{MOVE_ACTION}(1)", "Down", show=False, priority=True),
        Binding("home", f"{JUMP_ACTION}(-1)", "First", show=False, priority=True),
        Binding("end", f"{JUMP_ACTION}(1)", "Last", show=False, priority=True),
    ]
    DEFAULT_CSS = (
        POPUP_CSS
        + f"""
    #{ACTIONS_TITLE} {{ text-wrap: nowrap; text-overflow: ellipsis; }}
    #{ACTIONS_LIST} {{
        border: none; padding: 0; background: $surface;
        scrollbar-size-vertical: $scrollbar-width;
    }}
    #{ACTIONS_LIST} > ListItem {{
        height: 1; padding: 0; border: none; background: $surface; color: $primary-text;
    }}
    #{ACTIONS_LIST} > ListItem > Static {{
        width: 100%; height: 1; padding: 0; color: $primary-text;
        text-wrap: nowrap; text-overflow: ellipsis;
    }}
    #{ACTIONS_LIST} > ListItem:disabled, #{ACTIONS_LIST} > ListItem:disabled > Static {{
        color: $secondary-text; text-opacity: 100%; opacity: 100%;
    }}
    #{ACTIONS_LIST} > ListItem.-highlight,
    #{ACTIONS_LIST} > ListItem.-highlight > Static {{
        outline: none; background: $selected-surface; color: $primary-text; text-style: none;
    }}
    #{ACTIONS_LIST}:focus {{ background-tint: transparent; }}
    #{ACTIONS_HINT} {{ height: auto; margin-top: 1; }}
    #{ACTIONS_FRAME}.{COMPACT_CLASS} {{ margin: 0 1; padding: 0 1; }}
    """
    )

    def __init__(
        self,
        queries: OperatorQueries,
        open_popup: PopupFactory,
        request: PaletteRequest,
        *,
        on_change: OnChangeRegistration,
        action_confirmation_seconds: float,
        task_routes_available: bool = False,
        project_diagnostics_available: bool = False,
        extensions: tuple[ExtensionEntry, ...] = (),
        resolution_screen: ResolutionScreenFactory | None = None,
    ) -> None:
        """Offer the actions for `request`'s task, or the project when it has none."""
        super().__init__()
        prepare_popup(self)
        self._queries = queries
        self._open_popup = open_popup
        self._request = replace(request, action=None)
        self._on_change = on_change
        self._confirmation_seconds = action_confirmation_seconds
        self._task_routes_available = task_routes_available
        self._project_diagnostics_available = project_diagnostics_available
        self._extensions = extensions
        self._resolution_screen = resolution_screen
        self._expanded = False
        self._finishing = False
        self._confirmed: str | None = None
        self._confirmation_timer: Timer | None = None
        self._shown: tuple[tuple[str, str], ...] | None = None
        self._groups: tuple[ActionGroup, ...] = ()
        self._change_worker: Worker[None] | None = None
        self._sync_worker: Worker[None] | None = None

    def compose(self) -> ComposeResult:
        """Render the header, the grouped rows and the hint line."""
        with PopupFrame(widget_id=ACTIONS_FRAME, classes=POPUP_FRAME_CLASS):
            with Horizontal(classes=POPUP_HEADER_CLASS):
                yield _TitleLabel(self._paint_title, widget_id=ACTIONS_TITLE)
                yield Button(
                    popup_close_label(), id=ACTIONS_CLOSE_BUTTON, classes=POPUP_CLOSE_CLASS
                )
            yield Rule(classes=POPUP_RULE_CLASS)
            yield _ActionList(id=ACTIONS_LIST, classes=POPUP_BODY_CLASS)
            yield Static(id=ACTIONS_HINT, markup=False)

    async def on_mount(self) -> None:
        """Build the rows, select the first one and follow database changes."""
        await self._sync(select_first=True)
        self._apply_compact_layout(self.size.width, self.size.height)
        self._list.focus()
        self.call_after_refresh(self.query_one(f"#{ACTIONS_FRAME}", PopupFrame).content_ready)
        self._change_worker = self.run_worker(
            self._on_change(self._refresh_after_database_change), group=CHANGE_WORKER
        )

    def on_unmount(self) -> None:
        """Stop following database changes and the confirmation timer."""
        if self._change_worker is not None:
            self._change_worker.cancel()
        if self._confirmation_timer is not None:
            self._confirmation_timer.stop()

    def on_resize(self, event: events.Resize) -> None:
        """Refit the frame, the title and the hint to the new size."""
        self._apply_compact_layout(event.size.width, event.size.height)
        frame = self.query_one(f"#{ACTIONS_FRAME}", PopupFrame)
        frame.reset_preferred_size()
        self.call_after_refresh(frame.content_ready)

    def _apply_compact_layout(self, width: int, height: int) -> None:
        frame = self.query_one(f"#{ACTIONS_FRAME}", PopupFrame)
        frame.set_class(width <= COMPACT_MAX_COLUMNS or height <= COMPACT_MAX_ROWS, COMPACT_CLASS)
        self.query_one(f"#{ACTIONS_HINT}", Static).update(
            WrappedGroups(
                (
                    key_hint(HINT_SELECT, SECONDARY_TEXT_COLOR),
                    key_hint(HINT_OPEN, SECONDARY_TEXT_COLOR),
                ),
                HINT_GAP,
            )
        )

    @property
    def _host(self) -> App[None]:
        return cast("App[None]", self.app)

    @property
    def _list(self) -> ListView:
        return self.query_one(f"#{ACTIONS_LIST}", ListView)

    @property
    def _selectable(self) -> list[int]:
        return [i for i, item in enumerate(self._list.children) if isinstance(item, ActionItem)]

    def _list_context(self) -> ListContext:
        task_id = self._request.task_id
        task_exists = task_id is not None and self._queries.get_task_state(task_id) is not None
        return ListContext(
            has_task=task_exists,
            task_actions=self._task_actions(task_id) if task_exists else frozenset(),
            answerable=task_exists and self._answerable_escalation_id() is not None,
            expanded=self._expanded,
            routes=self._task_routes_available,
            diagnostics=self._project_diagnostics_available,
            extensions=self._extensions,
        )

    def _task_actions(self, task_id: str | None) -> frozenset[PaletteAction]:
        if task_id is None:
            return frozenset()
        task = self._queries.get_task_state(task_id)
        if task is None:
            return frozenset()
        enabled = {
            PaletteAction(state.action.value)
            for state in self._queries.task_action_availability(task_id, None)
            if state.enabled
            or (
                state.action is OperatorTaskAction.RESUME
                and bool(task.holds)
                and state.disabled_reason == TASK_ACTION_OTHER_ROLE_TAKEN_OVER
            )
        }
        return frozenset(enabled | {PaletteAction.EXPORT})

    def _answerable_escalation_id(self) -> str | None:
        task_id = self._request.task_id
        for item in self._queries.list_escalations():
            escalation = item.escalation
            if escalation.task_id != task_id or escalation.status is not EscalationStatus.OPEN:
                continue
            answerable = any(option.label is ResolutionKind.ANSWER for option in escalation.options)
            if (
                answerable
                and self._queries.escalation_resolution_disabled_reason(escalation.id) is None
            ):
                return escalation.id
        return None

    async def _sync(self, *, select_first: bool = False) -> None:
        groups = action_groups(self._list_context())
        self._paint_title()
        layout = tuple((group.title, row.key) for group in groups for row in group.rows)
        keys = [key for _, key in layout]
        selection = self._selected_key()
        position = self._selected_position()
        if layout != self._shown:
            self._shown = None
            await self._rebuild(groups)
            self._shown = layout
        else:
            self._repaint(groups)
        if select_first:
            self._select_row(0)
        elif selection in keys:
            self._select_row(keys.index(selection))
        else:
            self._select_row(position)

    def _finish(self, result: PaletteReturn | None) -> None:
        self._finishing = True
        if self._sync_worker is not None:
            self._sync_worker.cancel()
        self.dismiss(result)

    def _schedule_sync(self, *, select_first: bool = False) -> None:
        if self._finishing:
            return
        self._sync_worker = self.run_worker(
            partial(self._sync, select_first=select_first), group=SYNC_WORKER, exclusive=True
        )

    def _selected_position(self) -> int:
        indices = self._selectable
        index = self._list.index
        return indices.index(index) if index in indices else 0

    async def _rebuild(self, groups: tuple[ActionGroup, ...]) -> None:
        await self._list.clear()
        items: list[ListItem] = []
        for group in groups:
            items.append(GroupTitleItem(group.title))
            items.extend(
                ActionItem(row, group.label_width, confirmed=self._is_confirmed(row))
                for row in group.rows
            )
        await self._list.extend(items)

    def _repaint(self, groups: tuple[ActionGroup, ...]) -> None:
        widths = {row.key: group.label_width for group in groups for row in group.rows}
        rows = {row.key: row for group in groups for row in group.rows}
        for item in self._list.children:
            if isinstance(item, ActionItem):
                key = item.row.key
                item.show(rows[key], widths[key], confirmed=self._is_confirmed(rows[key]))

    def _is_confirmed(self, row: ActionRow) -> bool:
        return self._confirmed is not None and row.key == f"{ACTION_ROW_PREFIX}{self._confirmed}"

    def _selected_key(self) -> str | None:
        item = self._list.highlighted_child
        return item.row.key if isinstance(item, ActionItem) else None

    def _select_row(self, position: int) -> None:
        indices = self._selectable
        if indices:
            self._list.index = indices[min(max(position, 0), len(indices) - 1)]

    def _paint_title(self) -> None:
        if not self.is_attached:
            return
        task_id = self._request.task_id
        task = self._queries.get_task_state(task_id) if task_id is not None else None
        title = self.query_one(f"#{ACTIONS_TITLE}", Label)
        if task_id is None or task is None:
            title.update(list_title(None, "", None))
            return
        title.update(list_title(task_id, task.title, self._display_role(task_id), title.size.width))

    def _display_role(self, task_id: str) -> str:
        return self._request.role or self._queries.task_action_role(task_id).value

    def action_move(self, step: int) -> None:
        """Move the selection by `step` rows, wrapping from the last row to the first."""
        indices = self._selectable
        if not indices:
            return
        current = self._list.index
        position = indices.index(current) if current in indices else -1
        self._list.index = indices[(position + step) % len(indices)]

    def action_jump(self, direction: int) -> None:
        """Select the first (`-1`) or last (`1`) row."""
        indices = self._selectable
        if indices:
            self._list.index = indices[0 if direction < 0 else -1]
            if direction < 0:
                self._list.scroll_home(animate=False)

    def action_close(self) -> None:
        """Close the list without choosing anything."""
        self._finish(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Close the list from its close control."""
        if event.button.id == ACTIONS_CLOSE_BUTTON:
            self._finish(None)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Open what the chosen row names."""
        if isinstance(event.item, ActionItem):
            self._open_row(event.item.row.key)

    def _open_row(self, key: str) -> None:
        if key == MORE_ROW:
            self._expanded = not self._expanded
            self._schedule_sync()
        elif key == SWITCH_ROW:
            self._push_over(
                TaskPickerScreen(self._queries, self._request.task_id), self._task_chosen
            )
        elif key == ANSWER_ROW:
            self._open_resolution()
        elif key.startswith(ACTION_ROW_PREFIX):
            action = PaletteAction(key.removeprefix(ACTION_ROW_PREFIX))
            self._push_over(self._open_popup(self._popup_request(action)), self._popup_closed)
        elif key.startswith(ROUTE_ROW_PREFIX):
            task_id = self._request.task_id
            if task_id is not None:
                route = TaskPaletteRoute(key.removeprefix(ROUTE_ROW_PREFIX))
                self._finish(TaskRouteChoice(task_id, route))
        elif key.startswith(EXTENSION_ROW_PREFIX):
            self._finish(self._extensions[int(key.removeprefix(EXTENSION_ROW_PREFIX))])

    def _push_over[Result](
        self, screen: Screen[Result], callback: Callable[[Result | None], None]
    ) -> None:
        self.covered = True

        def uncovered(result: Result | None) -> None:
            self.covered = False
            self.refresh(layout=True)
            callback(result)

        self._host.push_screen(screen, uncovered)

    def _popup_request(self, action: PaletteAction) -> PaletteRequest:
        return replace(self._request, action=action.value, role=self._role_for(action))

    def _role_for(self, action: PaletteAction) -> str | None:
        task_id = self._request.task_id
        if self._request.role is not None or task_id is None or action not in ROLE_ACTIONS:
            return self._request.role
        selected = OperatorTaskAction(action.value)
        default = self._queries.task_action_role(task_id)
        for role in (default, *(role for role in TASK_ACTION_ROLES if role is not default)):
            state = next(
                item
                for item in self._queries.task_action_availability(task_id, role)
                if item.action is selected
            )
            if state.enabled:
                return role.value if role is not default else None
        return None

    def _popup_closed(self, queued: str | None) -> None:
        if queued is None:
            return
        self._confirmed = queued
        if self._confirmation_timer is not None:
            self._confirmation_timer.stop()
        self._confirmation_timer = self.set_timer(
            self._confirmation_seconds, self._end_confirmation
        )
        self._schedule_sync()

    def _end_confirmation(self) -> None:
        if self._confirmation_timer is not None:
            self._confirmation_timer.stop()
        self._confirmed = None
        self._confirmation_timer = None
        self._schedule_sync()

    def _task_chosen(self, task_id: str | None) -> None:
        if task_id is None:
            return
        if task_id != self._request.task_id:
            self._request = replace(self._request, task_id=task_id, role=None)
            self._end_confirmation()
        self._expanded = False
        self._schedule_sync(select_first=True)

    def _open_resolution(self) -> None:
        escalation_id = self._answerable_escalation_id()
        if escalation_id is None:
            return
        if self._resolution_screen is None:
            raise ConfigError(RESOLUTION_NOT_CONFIGURED)
        self._push_over(self._resolution_screen(escalation_id), self._resolution_closed)

    def _resolution_closed(self, sent: bool | None) -> None:
        if sent:
            self._finish(None)

    async def _refresh_after_database_change(self) -> None:
        if self.is_attached:
            self._schedule_sync()
