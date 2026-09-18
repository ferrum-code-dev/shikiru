"""The dashboard widget plugin contract (docs/design/06-operator.md §5.4).

A widget is a plugin registered under the `shikiru.widgets` entry point group. `DashboardApp`
loads one per name in `dashboard.widgets` (05 §1) and builds it from a `WidgetContext`, so a
widget never reaches the state DB, tmux or git directly: it reads `context.state` and calls
`context.services` for anything else.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from textual.widget import Widget

    from shikiru.store.reader import StateReader

WIDGET_ENTRY_POINT_GROUP = "shikiru.widgets"


@dataclass(frozen=True)
class RepoStatus:
    """One repo's checkout state, as shown by the `header` widget (06 §5.4)."""

    path: str
    branch: str
    ahead: int
    behind: int
    dirty: int


class WidgetServices(Protocol):
    """Services `WidgetContext` supplies beyond `StateReader`, wired by `bootstrap` (06 §5.4)."""

    async def repo_status(self) -> Sequence[RepoStatus]:
        """Return the project's repos' checkout state, cached for at most 10 s."""
        ...

    def engine_up(self) -> bool:
        """Return whether the engine currently holds `.shikiru/engine.lock`."""
        ...


@dataclass(frozen=True)
class WidgetContext:
    """Everything a `DashboardWidget` needs to build its `Widget` (06 §5.4)."""

    state: StateReader
    project_dir: Path
    focus_task: str | None
    services: WidgetServices


class DashboardWidget(Protocol):
    """A system-pane panel plugin, registered under `WIDGET_ENTRY_POINT_GROUP` (06 §5.4)."""

    name: str

    def create(self, context: WidgetContext) -> Widget:
        """Build this widget's `textual.widget.Widget` from `context`."""
        ...
