"""The disk sampler measures the real project filesystem and `.shikiru` folder on their cadences."""

from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from shikiru.paths import project_state_dir
from shikiru.telemetry.disk import DiskSampler, tree_size
from tests.support.clock import FakeClock
from tests.support.permissions import requires_enforced_permissions
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from shikiru.telemetry.disk import DiskSnapshot

START = datetime(2026, 1, 1, tzinfo=UTC)
REFRESH_SECONDS = 60.0
SIZE_REFRESH_SECONDS = 300.0
WAIT_SECONDS = 5
PRIVATE_DIRECTORY_MODE = 0o700
UNREADABLE_MODE = 0o000


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(START)


def _sampler(clock: FakeClock, project: Path) -> DiskSampler:
    return DiskSampler(
        clock, project, refresh_seconds=REFRESH_SECONDS, size_refresh_seconds=SIZE_REFRESH_SECONDS
    )


def _write(project: Path, relative: str, size: int) -> Path:
    path = project_state_dir(project) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


async def _wait_for(
    sampler: DiskSampler, condition: Callable[[DiskSnapshot], bool]
) -> DiskSnapshot:
    latest: list[DiskSnapshot] = []

    async def reached() -> bool:
        latest[:] = [await sampler.sample_if_due()]
        return condition(latest[0])

    await wait_until(reached, timeout=WAIT_SECONDS)
    return latest[0]


async def _measured(sampler: DiskSampler) -> DiskSnapshot:
    return await _wait_for(sampler, lambda snapshot: snapshot.shikiru_bytes is not None)


def test_the_folder_size_counts_apparent_bytes_once_per_file_without_following_symlinks(
    tmp_path: Path,
) -> None:
    root = project_state_dir(tmp_path)
    assert tree_size(root) == 0
    original = _write(tmp_path, "worktrees/T-1/data.bin", 1000)
    os.link(original, original.with_name("linked.bin"))
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "big.bin").write_bytes(b"x" * 5000)
    (root / "runs").mkdir()
    (root / "runs" / "current").symlink_to(outside, target_is_directory=True)
    _write(tmp_path, "runs/T-1/prompt.md", 500)

    assert tree_size(root) == 1000 + 500


@requires_enforced_permissions
def test_an_unreadable_subtree_fails_the_folder_size(tmp_path: Path) -> None:
    _write(tmp_path, "worktrees/T-1/data.bin", 1000)
    locked = project_state_dir(tmp_path) / "worktrees"
    locked.chmod(UNREADABLE_MODE)
    try:
        with pytest.raises(PermissionError):
            tree_size(project_state_dir(tmp_path))
    finally:
        locked.chmod(PRIVATE_DIRECTORY_MODE)


async def test_free_space_comes_from_the_project_filesystem_and_the_size_is_measured_after_it(
    tmp_path: Path, clock: FakeClock
) -> None:
    _write(tmp_path, "state.db", 3500)
    sampler = _sampler(clock, tmp_path)

    first = await sampler.sample_if_due()
    measured = await _measured(sampler)

    assert first.error is None
    assert first.total_bytes == shutil.disk_usage(tmp_path).total
    assert first.free_bytes is not None
    assert 0 < first.free_bytes <= shutil.disk_usage(tmp_path).total
    assert measured.shikiru_bytes == 3500


async def test_a_missing_project_is_unavailable_with_no_observation(
    tmp_path: Path, clock: FakeClock
) -> None:
    snapshot = await _sampler(clock, tmp_path / "gone").sample_if_due()

    assert snapshot.error is not None
    assert not snapshot.failed
    assert snapshot.sampled_at is None
    assert snapshot.free_bytes is None


async def test_the_size_cadence_is_slower_than_the_free_space_cadence(
    tmp_path: Path, clock: FakeClock
) -> None:
    _write(tmp_path, "first.md", 1000)
    sampler = _sampler(clock, tmp_path)
    first = await _measured(sampler)
    _write(tmp_path, "second.md", 2000)

    clock.advance(REFRESH_SECONDS)
    free_refreshed = await sampler.sample_if_due()
    clock.advance(SIZE_REFRESH_SECONDS - REFRESH_SECONDS)
    await sampler.sample_if_due()
    resized = await _wait_for(
        sampler, lambda snapshot: snapshot.shikiru_bytes != first.shikiru_bytes
    )

    assert free_refreshed.sampled_at != first.sampled_at
    assert free_refreshed.shikiru_bytes == first.shikiru_bytes == 1000
    assert resized.shikiru_bytes == 3000


async def test_a_failed_free_space_sample_keeps_the_last_observation_and_a_recovery_clears_it(
    tmp_path: Path, clock: FakeClock
) -> None:
    project = tmp_path / "project"
    project.mkdir()
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
    assert (failed.sampled_at, failed.free_bytes) == (first.sampled_at, first.free_bytes)
    assert recovered.error is None


@requires_enforced_permissions
async def test_an_unreadable_subtree_fails_the_size_and_keeps_the_last_size(
    tmp_path: Path, clock: FakeClock
) -> None:
    _write(tmp_path, "worktrees/T-1/data.bin", 1000)
    sampler = _sampler(clock, tmp_path)
    first = await _measured(sampler)
    locked = project_state_dir(tmp_path) / "worktrees"
    locked.chmod(UNREADABLE_MODE)
    try:
        clock.advance(SIZE_REFRESH_SECONDS)
        snapshot = await _wait_for(sampler, lambda snapshot: snapshot.failed)
    finally:
        locked.chmod(PRIVATE_DIRECTORY_MODE)

    assert snapshot.error is not None
    assert snapshot.shikiru_bytes == first.shikiru_bytes
