"""The `docker` widget: container counts, problems and the top five by CPU (06 §5.4)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from rich.cells import cell_len
from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text

from shikiru.dashboard.contract import DiagnosticSampleState, WidgetPlacement
from shikiru.ui.compact import format_cpu_pct, format_memory
from shikiru.ui.gated_static import GatedStatic
from shikiru.ui.screen import widget_is_attached
from shikiru.ui.section import (
    BOLD_PRIMARY,
    FAILED_WORD,
    GROUP_SEPARATOR,
    UNAVAILABLE_WORD,
    UNKNOWN_AGE,
    Row,
    Section,
    grey,
    labelled_rows,
    number,
    observation,
    state_line,
)
from shikiru.ui.tokens import (
    ALERT_COLOR,
    AMBER,
    SECONDARY_TEXT_COLOR,
)

if TYPE_CHECKING:
    from rich.console import Console, ConsoleOptions, RenderResult
    from textual.widget import Widget

    from shikiru.dashboard.contract import (
        ContainerProblemView,
        ContainerView,
        DockerSummaryView,
        DockerView,
        WidgetContext,
    )

SECTION_TITLE = "Docker"
LABEL_RUNNING = "Running"
LABEL_PROBLEMS = "Problems"
LABEL_ELSEWHERE = "Elsewhere"
LABEL_REFRESH = "Refresh"
TOP_HEADING = "Top 5 by CPU"
TOP_INDENT = 2
TOP_COLUMN_GAP = 1
TOP_GAP_COUNT = 2
RIGHT = "right"
ELLIPSIS = "ellipsis"
CPU_WORD = "CPU"
RAM_WORD = "RAM"
OF_WORD = "of"
RUNNING_WORD = "running"
USAGE_UNAVAILABLE = "usage unavailable"
NO_CONTAINERS = "No containers"
PROBLEM_UNHEALTHY = "unhealthy"
PROBLEM_RESTARTING = "restarting"
_WARNING_REASONS = frozenset({PROBLEM_UNHEALTHY, PROBLEM_RESTARTING})


def _measure(label: str, value: str) -> Text:
    return Text.assemble(grey(f"{label} "), number(value))


def _usage_groups(cpu_pct: float | None, memory_bytes: int | None) -> list[Text]:
    groups: list[Text] = []
    if cpu_pct is not None:
        groups.append(_measure(CPU_WORD, format_cpu_pct(cpu_pct)))
    if memory_bytes is not None:
        groups.append(_measure(RAM_WORD, format_memory(memory_bytes)))
    return groups


def _running_groups(summary: DockerSummaryView) -> list[Text]:
    counts = Text.assemble(
        number(str(summary.running)), grey(f" {OF_WORD} "), number(str(summary.total))
    )
    if not summary.usage_available:
        return [counts, Text(USAGE_UNAVAILABLE, style=AMBER)]
    return [counts, *_usage_groups(summary.cpu_pct, summary.memory_bytes)]


def _problem_group(problem: ContainerProblemView) -> Text:
    colour = AMBER if problem.reason in _WARNING_REASONS else ALERT_COLOR
    reason = (
        problem.reason if problem.exit_code is None else f"{problem.reason} ({problem.exit_code})"
    )
    return Text.assemble((problem.name, BOLD_PRIMARY), " ", (reason, colour))


def _elsewhere_groups(summary: DockerSummaryView) -> list[Text]:
    elsewhere = summary.elsewhere
    count = Text.assemble(number(str(elsewhere.running)), grey(f" {RUNNING_WORD}"))
    return [count, *_usage_groups(elsewhere.cpu_pct, elsewhere.memory_bytes)]


@dataclass(frozen=True)
class _TopContainers:
    containers: tuple[ContainerView, ...]

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        del console
        cpus = [format_cpu_pct(container.cpu_pct) for container in self.containers]
        memories = [format_memory(container.memory_bytes) for container in self.containers]
        cpu_width = max(map(len, cpus))
        memory_width = max(map(len, memories))
        name_width = max(
            1,
            options.max_width
            - TOP_INDENT
            - cpu_width
            - memory_width
            - TOP_GAP_COUNT * TOP_COLUMN_GAP,
        )
        grid = Table.grid(padding=(0, TOP_COLUMN_GAP, 0, 0))
        grid.add_column(width=TOP_INDENT - TOP_COLUMN_GAP)
        grid.add_column(width=min(name_width, max(len(c.name) for c in self.containers)))
        grid.add_column(justify=RIGHT, width=cpu_width)
        grid.add_column(justify=RIGHT, width=memory_width)
        for container, cpu, memory in zip(self.containers, cpus, memories, strict=True):
            name = Text(container.name, style=BOLD_PRIMARY, no_wrap=True)
            name.truncate(name_width, overflow=ELLIPSIS)
            grid.add_row("", name, number(cpu), number(memory))
        yield grid


@dataclass(frozen=True)
class _SummaryBody:
    summary: DockerSummaryView
    refresh_error: str | None

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        del console
        summary = self.summary
        head: list[Row] = [(grey(LABEL_RUNNING), tuple(_running_groups(summary)))]
        if summary.problems:
            problems = tuple(_problem_group(problem) for problem in summary.problems)
            head.append((grey(LABEL_PROBLEMS), problems))
        if self.refresh_error is not None:
            failure = Text(f"{FAILED_WORD}{GROUP_SEPARATOR}{self.refresh_error}", style=AMBER)
            head.append((grey(LABEL_REFRESH), (failure,)))
        tail: list[Row] = []
        if summary.elsewhere.running:
            tail.append((grey(LABEL_ELSEWHERE), tuple(_elsewhere_groups(summary))))
        label_width = max(cell_len(label.plain) for label, _ in [*head, *tail])
        yield labelled_rows(head, options.max_width, label_width)
        if summary.usage_available and summary.top:
            yield grey(TOP_HEADING)
            yield _TopContainers(summary.top)
        if tail:
            yield labelled_rows(tail, options.max_width, label_width)


def docker_section(snapshot: DockerView) -> Section:
    """Render `snapshot` as the Docker section in every state."""
    state = snapshot.state or (
        DiagnosticSampleState.AVAILABLE if snapshot.available else DiagnosticSampleState.UNAVAILABLE
    )
    ago = snapshot.sampled_ago or UNKNOWN_AGE
    if state is DiagnosticSampleState.UNAVAILABLE:
        return Section(
            SECTION_TITLE, state_line(UNAVAILABLE_WORD, SECONDARY_TEXT_COLOR, snapshot.error)
        )
    if state is DiagnosticSampleState.FAILED:
        return Section(SECTION_TITLE, state_line(FAILED_WORD, ALERT_COLOR, snapshot.error))
    if state is DiagnosticSampleState.EMPTY:
        return Section(SECTION_TITLE, grey(NO_CONTAINERS), observation(ago, stale=False))
    stale = state is DiagnosticSampleState.STALE
    body: RenderableType = Group()
    if snapshot.summary is not None:
        body = _SummaryBody(snapshot.summary, snapshot.error if stale else None)
    return Section(SECTION_TITLE, body, observation(ago, stale=stale))


class DockerPanel(GatedStatic):
    """Show the Docker section and re-render only when its content changed."""

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
        self.update_if_changed(docker_section(snapshot), key=snapshot)


class DockerWidget:
    """The `docker` dashboard widget plugin (06 §5.4)."""

    name = "docker"
    placement = WidgetPlacement.DIAGNOSTICS

    def create(self, context: WidgetContext) -> Widget:
        """Build the Docker panel for `context`."""
        return DockerPanel(context)
