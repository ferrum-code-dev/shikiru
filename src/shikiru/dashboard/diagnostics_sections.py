"""The permanent Resources and Engine sections of the Project diagnostics popup."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from rich.cells import cell_len
from rich.console import Group, RenderableType
from rich.text import Text

from shikiru.dashboard.contract import DiagnosticSampleState
from shikiru.ui.age import format_duration
from shikiru.ui.compact import BYTES_PER_GIB, RESOURCE_STATE_UNKNOWN, format_cpu_pct, format_gib
from shikiru.ui.section import (
    FAILED_WORD,
    GROUP_SEPARATOR,
    UNAVAILABLE_WORD,
    Row,
    Section,
    grey,
    labelled_rows,
    number,
    row_name,
    state_line,
)
from shikiru.ui.tokens import ALERT_COLOR, AMBER, SAGE, SECONDARY_TEXT_COLOR

if TYPE_CHECKING:
    from rich.console import Console, ConsoleOptions, RenderResult

    from shikiru.dashboard.contract import EngineStatusView, ResourceView

RESOURCES_TITLE = "Resources"
ENGINE_TITLE = "Engine"
LABEL_HOST = "Host"
LABEL_SHIKIRU = "Shikiru"
LABEL_AGENTS = "Agents"
LABEL_REFRESH = "Refresh"
LABEL_STATUS = "Status"
LABEL_FAULTS = "Faults"
CPU_WORD = "CPU"
RAM_WORD = "RAM"
RSS_WORD = "RSS"
GIB_UNIT = "GiB"
RAM_DIVIDER = " / "
NO_SAMPLE_RECORDED = "no sample recorded"
PROCESS_TREE_EMPTY = "process tree is empty"
CPU_RSS_UNAVAILABLE = "CPU/RSS unavailable"
RAM_UNAVAILABLE = "RAM used/total unavailable"
STATUS_RUNNING = "Running"
STATUS_NOT_RUNNING = "Not running"
UPTIME_PREFIX = "up"
UPTIME_UNAVAILABLE = "uptime unavailable"
FAULTS_NONE = "None open"
FAULTS_OPEN = "open"
_OBSERVED_STATES = frozenset({DiagnosticSampleState.AVAILABLE, DiagnosticSampleState.EMPTY})


def _measure(word: str, value: Text) -> Text:
    return Text.assemble(grey(f"{word} "), value)


def _host_groups(sample: ResourceView) -> tuple[Text, ...]:
    cpu = _measure(CPU_WORD, number(format_cpu_pct(sample.host_cpu_pct)))
    used = sample.host_ram_used_bytes
    total = sample.host_ram_total_bytes
    if used is None or total is None:
        return (cpu, grey(RAM_UNAVAILABLE))
    memory = Text.assemble(
        number(format_gib(used / BYTES_PER_GIB)),
        grey(RAM_DIVIDER),
        number(f"{format_gib(total / BYTES_PER_GIB)} {GIB_UNIT}"),
    )
    return (cpu, _measure(RAM_WORD, memory))


def _process_groups(
    cpu_pct: float | None, rss_gb: float | None, *, tree_empty: bool | None
) -> tuple[Text, ...]:
    if cpu_pct is None or rss_gb is None:
        return (grey(PROCESS_TREE_EMPTY if tree_empty else CPU_RSS_UNAVAILABLE),)
    return (
        _measure(CPU_WORD, number(format_cpu_pct(cpu_pct))),
        _measure(RSS_WORD, number(f"{format_gib(rss_gb)} {GIB_UNIT}")),
    )


def _failure(error: str) -> Text:
    return Text(f"{FAILED_WORD}{GROUP_SEPARATOR}{error}", style=AMBER)


@dataclass(frozen=True)
class _ResourceRows:
    sample: ResourceView
    refresh_error: str | None

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        del console
        sample = self.sample
        rows: list[Row] = [
            (row_name(LABEL_HOST), _host_groups(sample)),
            (
                row_name(LABEL_SHIKIRU),
                _process_groups(
                    sample.shikiru_cpu_pct,
                    sample.shikiru_rss_gb,
                    tree_empty=sample.shikiru_process_tree_empty,
                ),
            ),
            (
                row_name(LABEL_AGENTS),
                _process_groups(
                    sample.agents_cpu_pct,
                    sample.agents_rss_gb,
                    tree_empty=sample.agents_process_tree_empty,
                ),
            ),
        ]
        if self.refresh_error is not None:
            rows.append((grey(LABEL_REFRESH), (_failure(self.refresh_error),)))
        label_width = max(cell_len(label.plain) for label, _ in rows)
        yield labelled_rows(rows, options.max_width, label_width)


def resources_section(sample: ResourceView | None) -> Section:
    """Render `sample` as the unruled first Resources section in every state, with no age hint."""
    if sample is None:
        return Section(
            RESOURCES_TITLE,
            state_line(UNAVAILABLE_WORD, SECONDARY_TEXT_COLOR, NO_SAMPLE_RECORDED),
            ruled=False,
        )
    if sample.state is DiagnosticSampleState.FAILED:
        return Section(
            RESOURCES_TITLE, state_line(FAILED_WORD, ALERT_COLOR, sample.error), ruled=False
        )
    if sample.state is DiagnosticSampleState.UNAVAILABLE:
        return Section(
            RESOURCES_TITLE,
            state_line(UNAVAILABLE_WORD, SECONDARY_TEXT_COLOR, sample.error),
            ruled=False,
        )
    stale = sample.state is DiagnosticSampleState.STALE
    rows = _ResourceRows(sample, sample.error if stale else None)
    body: RenderableType = rows
    if not stale and sample.state not in _OBSERVED_STATES:
        body = Group(grey(RESOURCE_STATE_UNKNOWN), rows)
    return Section(RESOURCES_TITLE, body, ruled=False)


def _status_groups(status: EngineStatusView) -> tuple[Text, ...]:
    if not status.up:
        return (Text(STATUS_NOT_RUNNING, style=ALERT_COLOR),)
    uptime = (
        UPTIME_UNAVAILABLE
        if status.uptime_seconds is None
        else f"{UPTIME_PREFIX} {format_duration(status.uptime_seconds)}"
    )
    return (Text(STATUS_RUNNING, style=SAGE), grey(uptime))


def _faults_groups(fault_count: int) -> tuple[Text, ...]:
    if not fault_count:
        return (grey(FAULTS_NONE),)
    return (Text(f"{fault_count} {FAULTS_OPEN}", style=AMBER),)


@dataclass(frozen=True)
class _EngineRows:
    status: EngineStatusView
    fault_count: int

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        del console
        rows: list[Row] = [
            (grey(LABEL_STATUS), _status_groups(self.status)),
            (grey(LABEL_FAULTS), _faults_groups(self.fault_count)),
        ]
        label_width = max(cell_len(label.plain) for label, _ in rows)
        yield labelled_rows(rows, options.max_width, label_width)


def engine_section(status: EngineStatusView, fault_count: int) -> Section:
    """Render engine liveness and the number of open faults as the Engine section."""
    return Section(ENGINE_TITLE, _EngineRows(status, fault_count))
