"""The `header` widget: project, engine state, slots and repos (docs/design/06-operator.md §5.4)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.widgets import Static

from shikiru.domain.tasks import Status

if TYPE_CHECKING:
    from textual.widget import Widget

    from shikiru.dashboard.contract import RepoStatus, WidgetContext

_ACTIVE_STATUSES = (Status.RUNNING, Status.INTEGRATING, Status.ESCALATED)


def _repo_line(status: RepoStatus) -> str:
    return (
        f"{status.path} · {status.branch} · +{status.ahead}/-{status.behind} · {status.dirty} dirty"
    )


class HeaderPanel(Static):
    """Shows the project name, engine state, active slots and every repo's checkout state."""

    def __init__(self, context: WidgetContext) -> None:
        """Hold `context`; nothing here does I/O until `on_mount`."""
        super().__init__()
        self._widget_context = context

    async def on_mount(self) -> None:
        """Render the current state, then refresh on every `StateReader.watch` change."""
        await self._refresh()
        self.run_worker(self._widget_context.state.watch(self._refresh), exclusive=True)

    async def _refresh(self) -> None:
        settings = self._widget_context.settings
        active = len(self._widget_context.state.list_tasks(statuses=_ACTIVE_STATUSES))
        max_parallel = settings.orchestration.max_parallel
        engine_state = "up" if self._widget_context.services.engine_up() else "down"
        repo_statuses = await self._widget_context.services.repo_status()
        summary = f"{settings.project.name} · engine {engine_state} · slots {active}/{max_parallel}"
        lines = [summary, *(_repo_line(status) for status in repo_statuses)]
        self.update("\n".join(lines))


class HeaderWidget:
    """The `header` dashboard widget plugin (06 §5.4)."""

    name = "header"

    def create(self, context: WidgetContext) -> Widget:
        """Build the `HeaderPanel` for `context`."""
        return HeaderPanel(context)
