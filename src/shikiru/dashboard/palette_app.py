"""The standalone tmux popup that shows the Actions list for the pane it was opened from."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.widgets import Static

from shikiru.dashboard.actions_list import ActionsListScreen
from shikiru.dashboard.contract import PaletteRequest
from shikiru.dashboard.covered import CoveredScreens
from shikiru.dashboard.palette import PaletteScreen
from shikiru.dashboard.palette_drafts import PaletteDrafts
from shikiru.ui.theme import install_theme

if TYPE_CHECKING:
    from textual.app import ComposeResult
    from textual.screen import ModalScreen

    from shikiru.dashboard.contract import OnChangeRegistration, ResolutionScreenFactory
    from shikiru.dashboard.palette import PaneSelector, StartEngine
    from shikiru.operator.commands import OperatorCommands
    from shikiru.operator.evidence_export import TaskEvidenceExporter
    from shikiru.operator.queries import OperatorQueries


class PaletteApp(CoveredScreens):
    """Run the Actions list without dashboard routes or Extensions in a tmux popup."""

    def __init__(
        self,
        commands: OperatorCommands,
        queries: OperatorQueries,
        pane_selector: PaneSelector,
        *,
        on_change: OnChangeRegistration,
        action_confirmation_seconds: float,
        engine_state_refresh_hz: int | None = None,
        start_engine: StartEngine | None = None,
        initial_context: PaletteRequest | None = None,
        evidence_exporter: TaskEvidenceExporter | None = None,
        resolution_screen: ResolutionScreenFactory | None = None,
    ) -> None:
        """Retain the services used by the list and its popups."""
        super().__init__()
        install_theme(self)
        self._commands = commands
        self._queries = queries
        self._pane_selector = pane_selector
        self._on_change = on_change
        self._action_confirmation_seconds = action_confirmation_seconds
        self._engine_state_refresh_hz = engine_state_refresh_hz
        self._start_engine = start_engine
        self._initial_context = initial_context or PaletteRequest()
        self._evidence_exporter = evidence_exporter
        self._resolution_screen = resolution_screen
        self._drafts = PaletteDrafts()

    def compose(self) -> ComposeResult:
        """Keep a neutral base screen beneath the modal list."""
        yield Static()

    async def on_mount(self) -> None:
        """Open the Actions list and exit with it."""
        self.push_screen(
            ActionsListScreen(
                self._queries,
                self._popup,
                self._initial_context,
                on_change=self._on_change,
                action_confirmation_seconds=self._action_confirmation_seconds,
                engine_state_refresh_hz=self._engine_state_refresh_hz,
                resolution_screen=self._resolution_screen,
            ),
            lambda _: self.exit(),
        )

    def _popup(self, request: PaletteRequest) -> ModalScreen[str | None]:
        return PaletteScreen(
            self._commands,
            self._queries,
            self._pane_selector,
            on_change=self._on_change,
            action_confirmation_seconds=self._action_confirmation_seconds,
            initial_context=self._drafts.attach(request),
            start_engine=self._start_engine,
            evidence_exporter=self._evidence_exporter,
            remember_contextual_draft=self._drafts.remember,
        )
