"""The `header` widget: project, engine state, slots and repo (docs/design/06-operator.md §5.4)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.widgets import Static

if TYPE_CHECKING:
    from textual.widget import Widget

    from shikiru.dashboard.contract import HeaderView, WidgetContext


def _header_text(view: HeaderView) -> str:
    engine_state = "up" if view.engine_up else "down"
    summary = (
        f"{view.project} · engine {engine_state} · slots {view.occupied_slots}/{view.maximum_slots}"
    )
    repo = view.repo
    repo_line = f"{repo.branch} · +{repo.ahead}/-{repo.behind} · {repo.dirty} dirty"
    return f"{summary}\n{repo_line}"


class HeaderPanel(Static):
    """Shows the project name, engine state, active slots and the checkout state."""

    def __init__(self, context: WidgetContext) -> None:
        """Hold `context`; nothing here does I/O until `on_mount`."""
        super().__init__()
        self._widget_context = context

    async def on_mount(self) -> None:
        """Render the current view, then refresh on every state change."""
        await self._refresh()
        self.run_worker(self._widget_context.on_change(self._refresh), exclusive=True)

    async def _refresh(self) -> None:
        self.update(_header_text(await self._widget_context.views.header()))


class HeaderWidget:
    """The `header` dashboard widget plugin (06 §5.4)."""

    name = "header"

    def create(self, context: WidgetContext) -> Widget:
        """Build the `HeaderPanel` for `context`."""
        return HeaderPanel(context)
