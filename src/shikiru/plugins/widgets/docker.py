"""The `docker` widget: live container CPU and memory (06 §5.4)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual.widgets import Static

from shikiru.dashboard.contract import DiagnosticSampleState, WidgetPlacement
from shikiru.ui.screen import widget_is_attached

if TYPE_CHECKING:
    from textual.widget import Widget

    from shikiru.dashboard.contract import (
        ContainerProblemView,
        DockerSummaryView,
        DockerView,
        WidgetContext,
    )

UNKNOWN_AGE = "\N{EN DASH}"
BYTES_PER_MIB = 1024**2


def _docker_text(snapshot: DockerView) -> str | Text:
    state = snapshot.state or (
        DiagnosticSampleState.AVAILABLE if snapshot.available else DiagnosticSampleState.UNAVAILABLE
    )
    if state in (DiagnosticSampleState.UNAVAILABLE, DiagnosticSampleState.FAILED):
        detail = f" · {snapshot.error}" if snapshot.error else ""
        return f"Docker {state.value}{detail}"
    if state is DiagnosticSampleState.EMPTY:
        return _sample_age("Docker available · no running containers", snapshot.sampled_ago)
    lines = _summary_lines(snapshot.summary)
    if state is DiagnosticSampleState.STALE:
        lines.insert(0, Text(f"Stale sample · observed {snapshot.sampled_ago or UNKNOWN_AGE} ago"))
        if snapshot.error is not None:
            lines.append(Text(f"Latest sample failed · {snapshot.error}"))
    elif snapshot.sampled_ago is not None:
        lines.insert(0, Text(f"Observed {snapshot.sampled_ago} ago"))
    return Text("\n").join(lines)


def _summary_lines(summary: DockerSummaryView | None) -> list[Text]:
    if summary is None:
        return []
    lines = [Text(f"Running {summary.running} of {summary.total}")]
    if not summary.usage_available:
        lines.append(Text("Usage unavailable"))
    lines.extend(Text(f"{problem.name} {_problem_text(problem)}") for problem in summary.problems)
    lines.extend(
        Text(f"{usage.name} · CPU {usage.cpu_pct:.0f}% · memory {_mib(usage.memory_bytes)}")
        for usage in summary.top
    )
    return lines


def _problem_text(problem: ContainerProblemView) -> str:
    if problem.exit_code is None:
        return problem.reason
    return f"{problem.reason} ({problem.exit_code})"


def _mib(memory_bytes: int) -> str:
    return f"{memory_bytes / BYTES_PER_MIB:.0f} MiB"


def _sample_age(message: str, sampled_ago: str | None) -> str:
    if sampled_ago is None:
        return message
    return f"{message} · observed {sampled_ago} ago"


class DockerPanel(Static):
    """Show Docker container usage and explicit unavailable or failed states."""

    def __init__(self, context: WidgetContext) -> None:
        """Hold `context`; Docker sampling starts on mount."""
        super().__init__(id="docker")
        self._widget_context = context

    async def on_mount(self) -> None:
        """Render and refresh Docker state at the configured cadence."""
        await self._refresh()
        self.set_interval(self._widget_context.docker_refresh_seconds, self._refresh)

    async def _refresh(self) -> None:
        if not widget_is_attached(self):
            return
        snapshot = await self._widget_context.views.containers()
        if not widget_is_attached(self):
            return
        self.update(_docker_text(snapshot))


class DockerWidget:
    """The `docker` dashboard widget plugin (06 §5.4)."""

    name = "docker"
    placement = WidgetPlacement.DIAGNOSTICS

    def create(self, context: WidgetContext) -> Widget:
        """Build the Docker panel for `context`."""
        return DockerPanel(context)
