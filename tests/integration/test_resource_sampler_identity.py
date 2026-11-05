"""Agent resource sampling: recorded matching processes only, one sample per interval."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import psutil
import pytest

from shikiru.config.loader import load_settings
from shikiru.domain.tasks import Role
from shikiru.engine.turn_processes import turn_pid_path
from shikiru.telemetry.resources import ResourceProcessRoots, ResourceSampler
from tests.support.clock import FakeClock
from tests.support.config import write_minimal_project
from tests.support.resource_agent import (
    RESOURCE_AGENT_WAIT_SECONDS,
    RESOURCE_BYTES_PER_GIB,
    RESOURCE_PID_REUSE_SHIFT_SECONDS,
    RESOURCE_RSS_TOLERANCE_RATIO,
    resource_services,
    start_resource_agent,
)
from tests.support.store import seed_task
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.store.reader import StateReader
    from tests.support.store import Connection
    from tests.support.tmux import TmuxServer

START = datetime(2026, 1, 1, tzinfo=UTC)


async def test_one_sample_serves_an_interval_and_rejects_reused_and_exited_identities(
    connection: Connection,
    reader: StateReader,
    tmp_path: Path,
    tmux_server: TmuxServer,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    settings = load_settings(project_dir)
    interval = settings.orchestration.resources.sample_seconds
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Check turn identity reuse")
    runner, process, turn_processes, task_run_dir, identity = await start_resource_agent(
        connection, clock, project_dir, task
    )
    services = resource_services(
        reader,
        project_dir,
        clock,
        settings=settings,
        tmux_server=tmux_server,
        sampler=ResourceSampler(clock, lambda: settings.orchestration.resources),
        runner=runner,
    )
    turn_pid = turn_pid_path(task_run_dir, Role.IMPLEMENTER)
    assert identity.started_at is not None
    try:
        first = await services.resources()
        assert first.agents_process_tree_empty is False

        reused = replace(
            identity, started_at=identity.started_at - RESOURCE_PID_REUSE_SHIFT_SECONDS
        )
        turn_pid.write_text(json.dumps(asdict(reused)), encoding="utf-8")
        within_interval = await services.resources()
        assert within_interval.sampled_at == first.sampled_at
        assert within_interval.agents_process_tree_empty is False

        clock.advance(interval)
        after_reuse = await services.resources()
        assert runner.is_running(identity)
        assert after_reuse.sampled_at != first.sampled_at
        assert after_reuse.agents_process_tree_empty is True
        assert after_reuse.agents_cpu_pct is None
        assert after_reuse.agents_rss_gb is None

        turn_processes.record(task_run_dir, Role.IMPLEMENTER, process.pid)
        await process.stop()
        await wait_until(
            lambda: not runner.is_running(identity), timeout=RESOURCE_AGENT_WAIT_SECONDS
        )
        clock.advance(interval)
        exited = await services.resources()
        assert exited.agents_process_tree_empty is True
        assert exited.agents_cpu_pct is None
        assert exited.agents_rss_gb is None
    finally:
        if process.returncode is None:
            await process.stop()
        turn_processes.clear(task_run_dir, Role.IMPLEMENTER)


async def test_overlapping_recorded_agent_roots_are_counted_once(
    connection: Connection,
    tmp_path: Path,
) -> None:
    project_dir = write_minimal_project(tmp_path)
    settings = load_settings(project_dir)
    clock = FakeClock(START)
    task = seed_task(connection, clock, title="Deduplicate recorded process roots")
    _, process, turn_processes, task_run_dir, identity = await start_resource_agent(
        connection, clock, project_dir, task
    )
    root = psutil.Process(identity.pid)
    sampler = ResourceSampler(clock, lambda: settings.orchestration.resources)
    roots = ResourceProcessRoots((identity,), (identity, identity))
    try:
        try:
            sample = sampler.sample_process_roots(roots)
            tree = [root, *root.children(recursive=True)]
            expected_rss_bytes = sum(item.memory_info().rss for item in tree)
        except psutil.AccessDenied, PermissionError:
            pytest.skip("host process enumeration is restricted")
        assert sample.shikiru_process_tree_empty is True
        assert sample.agents_process_tree_empty is False
        assert sample.agents_rss_gb is not None
        assert sample.instance_rss_gb == sample.agents_rss_gb
        assert sample.agents_rss_gb * RESOURCE_BYTES_PER_GIB == pytest.approx(
            expected_rss_bytes, rel=RESOURCE_RSS_TOLERANCE_RATIO
        )
    finally:
        if process.returncode is None:
            await process.stop()
        turn_processes.clear(task_run_dir, Role.IMPLEMENTER)
