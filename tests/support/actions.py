"""Drivers for the Actions list, its popups and the hosts that run them in tests."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any

from textual.widgets import Label, ListView, Static

from shikiru.dashboard.actions_list import (
    ACTIONS_LIST,
    MORE_LABEL,
    ActionItem,
    ActionsListScreen,
    GroupTitleItem,
)
from shikiru.dashboard.covered import CoveredScreens
from shikiru.dashboard.palette import SUBMIT_ACTION_LABELS, PaletteScreen
from shikiru.ui.theme import install_theme
from tests.support.cells import rendered_lines
from tests.support.waiting import ACTION_CONFIRMATION_SECONDS, UI_WAIT_SECONDS, wait_until

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from textual.pilot import Pilot
    from textual.screen import ModalScreen

    from shikiru.dashboard.contract import (
        ExtensionEntry,
        PaletteDraft,
        PaletteRequest,
        ResolutionScreenFactory,
    )
    from shikiru.dashboard.palette import StartEngine
    from shikiru.operator.commands import OperatorCommands
    from shikiru.operator.evidence_export import TaskEvidenceExporter
    from shikiru.operator.queries import OperatorQueries
    from shikiru.store.reader import StateReader

PENDING = "pending"
GROUP_MARK = "# "


class ScreenHost(CoveredScreens):
    """Run one screen and exit when it is dismissed, keeping its result."""

    def __init__(self, screen: ModalScreen[Any]) -> None:
        super().__init__()
        install_theme(self)
        self.hosted = screen
        self.result: object = PENDING

    def on_mount(self) -> None:
        self.push_screen(self.hosted, self._finish)

    def _finish(self, result: object) -> None:
        self.result = result
        self.exit()


def popup_screen(
    commands: OperatorCommands,
    queries: OperatorQueries,
    reader: StateReader,
    pane_selector: Callable[[str], Awaitable[None]],
    request: PaletteRequest,
    *,
    action_confirmation_seconds: float = ACTION_CONFIRMATION_SECONDS,
    start_engine: StartEngine | None = None,
    evidence_exporter: TaskEvidenceExporter | None = None,
    remember_contextual_draft: Callable[[PaletteDraft], None] | None = None,
    attach_drafts: Callable[[PaletteRequest], PaletteRequest] | None = None,
) -> PaletteScreen:
    """Build the preset action popup for `request` the way the dashboard wiring does."""
    return PaletteScreen(
        commands,
        queries,
        pane_selector,
        on_change=reader.watch,
        action_confirmation_seconds=action_confirmation_seconds,
        initial_context=request if attach_drafts is None else attach_drafts(request),
        start_engine=start_engine,
        evidence_exporter=evidence_exporter,
        remember_contextual_draft=remember_contextual_draft,
    )


def actions_screen(
    commands: OperatorCommands,
    queries: OperatorQueries,
    reader: StateReader,
    pane_selector: Callable[[str], Awaitable[None]],
    request: PaletteRequest,
    *,
    action_confirmation_seconds: float = ACTION_CONFIRMATION_SECONDS,
    engine_state_refresh_hz: int | None = None,
    start_engine: StartEngine | None = None,
    evidence_exporter: TaskEvidenceExporter | None = None,
    remember_contextual_draft: Callable[[PaletteDraft], None] | None = None,
    attach_drafts: Callable[[PaletteRequest], PaletteRequest] | None = None,
    task_routes_available: bool = False,
    project_diagnostics_available: bool = False,
    extensions: tuple[ExtensionEntry, ...] = (),
    resolution_screen: ResolutionScreenFactory | None = None,
) -> ActionsListScreen:
    """Build the Actions list for `request` with popups wired as in the dashboard."""
    return ActionsListScreen(
        queries,
        partial(
            popup_screen,
            commands,
            queries,
            reader,
            pane_selector,
            action_confirmation_seconds=action_confirmation_seconds,
            start_engine=start_engine,
            evidence_exporter=evidence_exporter,
            remember_contextual_draft=remember_contextual_draft,
            attach_drafts=attach_drafts,
        ),
        request,
        on_change=reader.watch,
        action_confirmation_seconds=action_confirmation_seconds,
        engine_state_refresh_hz=engine_state_refresh_hz,
        task_routes_available=task_routes_available,
        project_diagnostics_available=project_diagnostics_available,
        extensions=extensions,
        resolution_screen=resolution_screen,
    )


def action_list(screen: ActionsListScreen) -> ListView:
    return screen.query_one(f"#{ACTIONS_LIST}", ListView)


def structure(screen: ActionsListScreen) -> list[str]:
    """List the group titles (marked with `# `) and row labels in display order."""
    return [
        f"{GROUP_MARK}{item.title}" if isinstance(item, GroupTitleItem) else item.row.label
        for item in action_list(screen).children
        if isinstance(item, (GroupTitleItem, ActionItem))
    ]


def selected_label(screen: ActionsListScreen) -> str | None:
    item = action_list(screen).highlighted_child
    return item.row.label if isinstance(item, ActionItem) else None


def row_line(screen: ActionsListScreen, label: str) -> str:
    """Return the painted text of the row labelled `label`."""
    text = _item(screen, label).query_one(Static)
    return "".join(char for char, _ in rendered_lines(text)[0])


def _item(screen: ActionsListScreen, label: str) -> ActionItem:
    return next(
        item
        for item in action_list(screen).children
        if isinstance(item, ActionItem) and item.row.label == label
    )


async def select_row(pilot: Pilot[None], screen: ActionsListScreen, label: str) -> None:
    """Move the list selection to the row labelled `label` with the keyboard."""
    for _ in range(len(action_list(screen).children)):
        if selected_label(screen) == label:
            return
        await pilot.press("down")
    message = f"row {label!r} is not in the list"
    raise AssertionError(message)


async def open_row(pilot: Pilot[None], screen: ActionsListScreen, label: str) -> None:
    """Select the row labelled `label` and press Enter."""
    await wait_until(lambda: label in structure(screen), timeout=UI_WAIT_SECONDS)
    await select_row(pilot, screen, label)
    await pilot.press("enter")


async def open_action(pilot: Pilot[None], screen: ActionsListScreen, action: str) -> None:
    """Open the row for palette action `action`, expanding More when it hides there."""
    label = SUBMIT_ACTION_LABELS[action]
    await wait_until(lambda: bool(structure(screen)), timeout=UI_WAIT_SECONDS)
    if label not in structure(screen):
        await open_row(pilot, screen, MORE_LABEL)
        await wait_until(lambda: label in structure(screen), timeout=UI_WAIT_SECONDS)
    await open_row(pilot, screen, label)


def title_text(screen: ModalScreen[Any]) -> str:
    """Return the popup title text of an Actions list or preset popup."""
    return str(screen.query_one(".popup-title", Label).content)
