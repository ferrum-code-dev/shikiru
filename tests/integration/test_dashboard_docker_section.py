"""The Docker section renders counts, problems, top containers and every sampling state."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from shikiru.config.loader import load_settings
from shikiru.dashboard.contract import (
    ContainerProblemView,
    ContainerView,
    DiagnosticSampleState,
    DockerSummaryView,
    DockerView,
    ElsewhereView,
)
from shikiru.plugins.widgets.docker import DockerPanel
from tests.support.cells import rendered_lines
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.dashboard import StubServices, WidgetHostApp, widget_context
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.store.reader import StateReader

pytestmark = pytest.mark.usefixtures("colour_terminal", "connection")

_START = datetime(2026, 1, 1, tzinfo=UTC)
_WIDE = (60, 20)
_COMPACT = (30, 20)
_TINY = (20, 8)
_WAIT_SECONDS = 5
_REFRESH_SECONDS = 0.05
_GIB = 1024**3
_MIB = 1024**2

_TOP = (
    ContainerView("api", 38.0, 410 * _MIB),
    ContainerView("web", 5.0, int(1.2 * _GIB)),
    ContainerView("cache", 2.0, 96 * _MIB),
)
_PROBLEMS = (
    ContainerProblemView("web-db", "unhealthy"),
    ContainerProblemView("worker", "exited", 1),
)
_FULL = DockerSummaryView(
    running=3,
    total=5,
    cpu_pct=45.0,
    memory_bytes=int(1.9 * _GIB),
    problems=_PROBLEMS,
    top=_TOP,
    elsewhere=ElsewhereView(2, 3.0, 300 * _MIB),
    usage_available=True,
)
_QUIET = replace(
    _FULL,
    problems=(),
    top=(),
    elsewhere=ElsewhereView(0, None, None),
)
_NO_USAGE = replace(
    _FULL,
    cpu_pct=None,
    memory_bytes=None,
    problems=(ContainerProblemView("queue", "restarting"),),
    top=(),
    elsewhere=ElsewhereView(2, None, None),
    usage_available=False,
)


def _view(
    summary: DockerSummaryView | None,
    state: DiagnosticSampleState,
    *,
    error: str | None = None,
) -> DockerView:
    return DockerView(available=True, summary=summary, state=state, sampled_ago="3s", error=error)


class _Services(StubServices):
    def __init__(self, view: DockerView) -> None:
        super().__init__()
        self.view = view

    async def containers(self) -> DockerView:
        return self.view


_RUNNING_LINE = "Running 3 of 5 \u00b7 CPU 45% \u00b7 RAM 1.9 GiB"
_FULL_LINES = [
    "Docker",
    "Running   3 of 5 \u00b7 CPU 45% \u00b7 RAM 1.9 GiB",
    "Problems  web-db unhealthy \u00b7 worker exited (1)",
    "Top 5 by CPU",
    "  api    38% 410 MiB",
    "  web   5.0% 1.2 GiB",
    "  cache 2.0%  96 MiB",
    "Elsewhere 2 running \u00b7 CPU 3.0% \u00b7 RAM 300 MiB",
]
_QUIET_LINES = ["Docker", _RUNNING_LINE]
_NO_USAGE_LINES = [
    "Docker",
    "Running   3 of 5 \u00b7 usage unavailable",
    "Problems  queue restarting",
    "Elsewhere 2 running",
]
_STALE_LINES = [
    "Docker                               stale \u00b7 observed 3s ago",
    _RUNNING_LINE,
    "Refresh failed \u00b7 docker timed out",
]
_EMPTY_LINES = ["Docker", "No containers"]
_UNAVAILABLE_LINES = ["Docker", "unavailable"]
_FAILED_LINES = ["Docker", "failed \u00b7 invalid JSON"]


_AVAILABLE = DiagnosticSampleState.AVAILABLE
_WIDE_CASES = [
    pytest.param(_view(_FULL, _AVAILABLE), _FULL_LINES, id="full"),
    pytest.param(_view(_QUIET, _AVAILABLE), _QUIET_LINES, id="no-problems-no-elsewhere"),
    pytest.param(_view(_NO_USAGE, _AVAILABLE), _NO_USAGE_LINES, id="usage-unavailable"),
    pytest.param(
        _view(_QUIET, DiagnosticSampleState.STALE, error="docker timed out"),
        _STALE_LINES,
        id="stale-keeps-last-observation",
    ),
    pytest.param(_view(None, DiagnosticSampleState.EMPTY), _EMPTY_LINES, id="empty"),
    pytest.param(
        DockerView(available=False, state=DiagnosticSampleState.UNAVAILABLE),
        _UNAVAILABLE_LINES,
        id="unavailable",
    ),
    pytest.param(
        DockerView(available=False, state=DiagnosticSampleState.FAILED, error="invalid JSON"),
        _FAILED_LINES,
        id="failed",
    ),
]


def _panel(reader: StateReader, tmp_path: Path, view: DockerView, **context: float) -> DockerPanel:
    base = widget_context(
        reader, write_minimal_project(tmp_path), FakeClock(_START), services=_Services(view)
    )
    return DockerPanel(replace(base, **context))


async def _settled(panel: DockerPanel) -> None:
    await wait_until(lambda: any(rendered_lines(panel)), timeout=_WAIT_SECONDS)


def _plain(panel: DockerPanel) -> list[str]:
    return ["".join(char for char, _ in cells) for cells in rendered_lines(panel)]


@pytest.mark.parametrize(("view", "expected"), _WIDE_CASES)
async def test_each_state_renders_its_rows(
    reader: StateReader, tmp_path: Path, view: DockerView, expected: list[str]
) -> None:
    panel = _panel(reader, tmp_path, view)
    async with WidgetHostApp(panel).run_test(size=_WIDE):
        await _settled(panel)
        assert [text.rstrip() for text in _plain(panel) if text.strip()] == expected


_COMPACT_LAYOUT = [
    "Docker",
    "Running",
    "3 of 5 \u00b7 CPU 45% \u00b7 RAM 1.9 GiB",
    "Problems",
    "web-db unhealthy",
    "worker exited (1)",
    "Top 5 by CPU",
    "  api    38% 410 MiB",
    "  web   5.0% 1.2 GiB",
    "  cache 2.0%  96 MiB",
    "Elsewhere",
    "2 running \u00b7 CPU 3.0%",
    "RAM 300 MiB",
]
_TINY_LAYOUT = [
    "Docker",
    "Running",
    "3 of 5 \u00b7 CPU 45%",
    "RAM 1.9 GiB",
    "Problems",
    "web-db unhealthy",
    "worker exited (1)",
    "Top 5 by CPU",
    "  api  38% 410 MiB",
    "  web 5.0% 1.2 GiB",
    "  ca\u2026 2.0%  96 MiB",
    "Elsewhere",
    "2 running",
    "CPU 3.0%",
    "RAM 300 MiB",
]


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        pytest.param(_COMPACT, _COMPACT_LAYOUT, id="compact"),
        pytest.param(_TINY, _TINY_LAYOUT, id="20x8"),
    ],
)
async def test_narrow_widths_wrap_only_between_groups_and_truncate_only_names(
    reader: StateReader, tmp_path: Path, size: tuple[int, int], expected: list[str]
) -> None:
    panel = _panel(reader, tmp_path, _view(_FULL, DiagnosticSampleState.AVAILABLE))
    async with WidgetHostApp(panel).run_test(size=size):
        await _settled(panel)
        assert [text.rstrip() for text in _plain(panel)][: len(expected)] == expected


async def test_a_new_sample_replaces_the_rendering_at_the_refresh_interval(
    reader: StateReader, tmp_path: Path
) -> None:
    services = _Services(_view(_QUIET, DiagnosticSampleState.AVAILABLE))
    project_dir = write_minimal_project(tmp_path)
    settings = load_settings(project_dir, [f"dashboard.docker_refresh_seconds={_REFRESH_SECONDS}"])
    panel = DockerPanel(
        widget_context(reader, project_dir, FakeClock(_START), services=services, settings=settings)
    )
    async with WidgetHostApp(panel).run_test(size=_WIDE):
        await wait_until(lambda: "3 of 5" in "\n".join(_plain(panel)), timeout=_WAIT_SECONDS)
        services.view = _view(replace(_QUIET, running=4), DiagnosticSampleState.AVAILABLE)
        await wait_until(lambda: "4 of 5" in "\n".join(_plain(panel)), timeout=_WAIT_SECONDS)
