"""The disk sampler measures the real project filesystem and run directory on its cadence."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from shikiru.paths import runs_root
from shikiru.telemetry.disk import DiskSampler
from tests.support.clock import FakeClock
from tests.support.permissions import requires_enforced_permissions

if TYPE_CHECKING:
    from pathlib import Path

START = datetime(2026, 1, 1, tzinfo=UTC)
REFRESH_SECONDS = 60.0
PRIVATE_DIRECTORY_MODE = 0o700
UNREADABLE_MODE = 0o000


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(START)


def _sampler(clock: FakeClock, project: Path) -> DiskSampler:
    return DiskSampler(clock, project, refresh_seconds=REFRESH_SECONDS)


def _write_run_file(project: Path, name: str, size: int) -> None:
    run = runs_root(project) / "T-1"
    run.mkdir(parents=True, exist_ok=True)
    (run / name).write_bytes(b"x" * size)


async def test_free_and_total_space_come_from_the_project_filesystem_and_runs_are_summed(
    tmp_path: Path, clock: FakeClock
) -> None:
    _write_run_file(tmp_path, "prompt.md", 1000)
    _write_run_file(tmp_path, "view.jsonl", 2500)

    snapshot = await _sampler(clock, tmp_path).sample_if_due()

    assert snapshot.error is None
    assert snapshot.total_bytes == shutil.disk_usage(tmp_path).total
    assert snapshot.free_bytes is not None
    assert 0 < snapshot.free_bytes <= shutil.disk_usage(tmp_path).total
    assert snapshot.runs_bytes == 3500


async def test_a_missing_run_directory_measures_as_no_runs_not_as_a_failure(
    tmp_path: Path, clock: FakeClock
) -> None:
    snapshot = await _sampler(clock, tmp_path).sample_if_due()

    assert snapshot.error is None
    assert snapshot.runs_bytes == 0


async def test_a_missing_project_is_unavailable_with_no_observation(
    tmp_path: Path, clock: FakeClock
) -> None:
    snapshot = await _sampler(clock, tmp_path / "gone").sample_if_due()

    assert snapshot.error is not None
    assert not snapshot.failed
    assert snapshot.sampled_at is None
    assert snapshot.free_bytes is None


async def test_measurements_are_reused_until_the_refresh_interval_has_passed(
    tmp_path: Path, clock: FakeClock
) -> None:
    _write_run_file(tmp_path, "first.md", 1000)
    sampler = _sampler(clock, tmp_path)
    first = await sampler.sample_if_due()
    _write_run_file(tmp_path, "second.md", 2000)

    clock.advance(REFRESH_SECONDS - 1)
    reused = await sampler.sample_if_due()
    clock.advance(1)
    refreshed = await sampler.sample_if_due()

    assert (first.runs_bytes, reused.runs_bytes, refreshed.runs_bytes) == (1000, 1000, 3000)


async def test_a_failed_later_sample_keeps_the_last_observation_and_a_recovery_clears_it(
    tmp_path: Path, clock: FakeClock
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_run_file(project, "prompt.md", 1000)
    sampler = _sampler(clock, project)
    first = await sampler.sample_if_due()
    shutil.rmtree(project)

    clock.advance(REFRESH_SECONDS)
    failed = await sampler.sample_if_due()
    project.mkdir()
    clock.advance(REFRESH_SECONDS)
    recovered = await sampler.sample_if_due()

    assert failed.error is not None
    assert not failed.failed
    assert (failed.sampled_at, failed.runs_bytes) == (first.sampled_at, first.runs_bytes)
    assert failed.free_bytes == first.free_bytes
    assert recovered.error is None


@requires_enforced_permissions
async def test_an_unreadable_run_directory_fails_the_sample_and_keeps_the_last_observation(
    tmp_path: Path, clock: FakeClock
) -> None:
    _write_run_file(tmp_path, "prompt.md", 1000)
    sampler = _sampler(clock, tmp_path)
    first = await sampler.sample_if_due()
    runs = runs_root(tmp_path)
    runs.chmod(UNREADABLE_MODE)
    try:
        clock.advance(REFRESH_SECONDS)
        failed = await sampler.sample_if_due()
    finally:
        runs.chmod(PRIVATE_DIRECTORY_MODE)

    assert failed.failed
    assert failed.error is not None
    assert (failed.sampled_at, failed.runs_bytes) == (first.sampled_at, first.runs_bytes)
