"""Agent-tree CPU as the sampler measures it over real, freshly spawned busy children."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import psutil
import pytest

from shikiru.clock import SystemClock
from shikiru.config.settings import ResourcesConfig
from shikiru.process import identify_pid
from shikiru.telemetry.resources import ResourceProcessRoots, ResourceSampler
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from tests.support.tmux import TmuxServer

BURN_SOURCE = "while True: pass"
RESPAWNING_WRAPPER = f'while :; do "{sys.executable}" -c "{BURN_SOURCE}"; done'
MIN_BURNED_SECONDS = 0.3
WAIT_SECONDS = 15.0
MAX_DUTY_FRACTION = 1.2
SAMPLE_CADENCE_SECONDS = 0.05
RESOURCES = ResourcesConfig(
    max_cpu_pct=85, min_free_ram_gb=4, sample_seconds=SAMPLE_CADENCE_SECONDS, cpu_window_seconds=30
)


PANE_PID_FORMAT = "#{pane_pid}"


@pytest.fixture
async def wrapper_pid(tmux_server: TmuxServer) -> int:
    result = await tmux_server.run(
        "new-session",
        "-d",
        "-P",
        "-F",
        PANE_PID_FORMAT,
        "-s",
        "cpu",
        "/bin/sh",
        "-c",
        RESPAWNING_WRAPPER,
    )
    return int(result.stdout.strip())


def _burner(wrapper_pid: int) -> psutil.Process | None:
    children = psutil.Process(wrapper_pid).children()
    return children[0] if children else None


def _has_burned(process: psutil.Process | None) -> bool:
    return process is not None and sum(process.cpu_times()[:2]) >= MIN_BURNED_SECONDS


async def test_agents_cpu_counts_a_busy_child_from_its_first_sample(
    wrapper_pid: int,
) -> None:
    cpu_count = psutil.cpu_count(logical=True)
    assert cpu_count is not None
    expected_pct = 100.0 / cpu_count
    sampler = ResourceSampler(SystemClock(), RESOURCES)
    root = identify_pid(wrapper_pid)
    assert root is not None
    roots = ResourceProcessRoots((), (root,))
    await wait_until(lambda: _has_burned(_burner(wrapper_pid)), WAIT_SECONDS)

    first = sampler.sample_process_roots(roots)
    assert first.agents_cpu_pct is not None
    assert 0 < first.agents_cpu_pct < MAX_DUTY_FRACTION * expected_pct

    replaced = _burner(wrapper_pid)
    assert replaced is not None
    replaced.kill()
    await wait_until(
        lambda: (
            (burner := _burner(wrapper_pid)) is not None
            and burner.pid != replaced.pid
            and _has_burned(burner)
        ),
        WAIT_SECONDS,
    )
    born_between_samples = sampler.sample_process_roots(roots)
    assert born_between_samples.agents_cpu_pct is not None
    assert 0 < born_between_samples.agents_cpu_pct < MAX_DUTY_FRACTION * expected_pct
