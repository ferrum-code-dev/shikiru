"""Resource sampling through psutil for the engine and an isolated tmux pane."""

from __future__ import annotations

from typing import TYPE_CHECKING

import psutil

from shikiru.telemetry.resources import RESOURCE_SAMPLE_SECONDS, ResourceSampler
from shikiru.tmux.client import FORMAT_PANE_ID, exact_target
from shikiru.tmux.session import SessionView, session_name
from tests.support.clock import FakeClock
from tests.support.engine import build_tmux
from tests.support.factories import TASK_CREATED_AT

if TYPE_CHECKING:
    from pathlib import Path

    from tests.support.tmux import TmuxServer


async def test_resource_sampler_includes_a_tmux_pane_process_tree(
    tmux_server: TmuxServer, tmp_path: Path
) -> None:
    tmux = build_tmux(tmux_server)
    await SessionView(tmux).ensure("test", tmp_path, home_command=("/bin/sh",))
    pane_id = (await tmux.list_panes(exact_target(session_name("test")), FORMAT_PANE_ID))[0]
    pane_pid = await tmux.pane_pid(pane_id)
    without_pane = ResourceSampler(FakeClock(TASK_CREATED_AT), lambda: ()).sample()
    with_pane = ResourceSampler(FakeClock(TASK_CREATED_AT), lambda: (pane_pid,)).sample()

    assert psutil.Process(pane_pid).is_running()
    assert with_pane.instance_rss_gb > without_pane.instance_rss_gb


def test_resource_sampler_respects_the_two_second_sampling_cadence() -> None:
    clock = FakeClock(TASK_CREATED_AT)
    sampler = ResourceSampler(clock, lambda: ())

    first = sampler.sample_if_due()
    assert sampler.sample_if_due() is first
    clock.advance(RESOURCE_SAMPLE_SECONDS)
    assert sampler.sample_if_due() is not first
