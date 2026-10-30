from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from shikiru.domain.docker import COMPOSE_WORKING_DIR_LABEL, ProblemKind
from shikiru.process import SubprocessRunner
from shikiru.telemetry.docker import DockerSampler
from tests.support.clock import FakeClock
from tests.support.permissions import EXECUTABLE_MODE

START = datetime(2026, 1, 1, tzinfo=UTC)
TIMEOUT_SECONDS = 5.0
SHORT_TIMEOUT_SECONDS = 1.5
REFRESH_SECONDS = 5.0
LONG_REFRESH_SECONDS = 3600.0
SHORT_REFRESH_SECONDS = 0.5
PROJECT = "/work/app"
MEBI = 1024**2
HANG_SCRIPT = "exec sleep 30"


def _ps(identity: str, name: str, state: str, status: str, working_dir: str | None) -> str:
    labels = f"{COMPOSE_WORKING_DIR_LABEL}={working_dir}" if working_dir else "a=b"
    return json.dumps(
        {"ID": identity, "Names": name, "State": state, "Status": status, "Labels": labels}
    )


def _stats(identity: str, name: str, cpu: str, memory: str) -> str:
    return json.dumps({"ID": identity, "Name": name, "CPUPerc": cpu, "MemUsage": memory})


def _install_docker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    ps: list[str] | str,
    stats: list[str] | str,
    ps_status: int = 0,
    stats_status: int = 0,
) -> None:
    def branch(name: str, output: list[str] | str, status: int) -> str:
        if output == HANG_SCRIPT:
            return f"{name}) {HANG_SCRIPT};;"
        body = "\\n".join(output) if isinstance(output, list) else output
        return f"{name}) printf '%b\\n' '{body}'; exit {status};;"

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "docker"
    script.write_text(
        f'#!/bin/sh\ncase "$1" in\n{branch("ps", ps, ps_status)}\n'
        f"{branch('stats', stats, stats_status)}\nesac\n",
        encoding="utf-8",
    )
    script.chmod(EXECUTABLE_MODE)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def _sampler(
    clock: FakeClock, roots: list[Path] | None, timeout: float = TIMEOUT_SECONDS
) -> DockerSampler:
    return DockerSampler(
        SubprocessRunner(),
        clock,
        refresh_seconds=lambda: REFRESH_SECONDS,
        timeout_seconds=timeout,
        project_roots=roots,
    )


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(START)


async def test_project_scope_ranks_problems_and_sums_elsewhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    _install_docker(
        tmp_path,
        monkeypatch,
        ps=[
            _ps("a1", "api", "running", "Up 1 hour", PROJECT),
            _ps(
                "b2", "web", "running", "Up 1 hour (unhealthy)", f"{PROJECT}/.shikiru/worktrees/T-1"
            ),
            _ps("c3", "worker", "exited", "Exited (1) 2m ago", PROJECT),
            _ps("d4", "done", "exited", "Exited (0) 2m ago", PROJECT),
            _ps("e5", "other", "running", "Up 1 hour", "/elsewhere"),
            _ps("f6", "plain", "running", "Up 1 hour", None),
        ],
        stats=[
            _stats("a1", "api", "38.0%", "400MiB / 2GiB"),
            _stats("b2", "web", "5.0%", "1GiB / 2GiB"),
            _stats("e5", "other", "2.0%", "100MiB / 2GiB"),
            _stats("f6", "plain", "1.0%", "50MiB / 2GiB"),
        ],
    )

    snapshot = await _sampler(clock, [Path(PROJECT)]).sample()

    summary = snapshot.summary
    assert snapshot.available
    assert summary is not None
    assert (summary.running, summary.total) == (2, 4)
    assert (summary.cpu_pct, summary.memory_bytes) == (43.0, 1424 * MEBI)
    assert [(p.name, p.kind) for p in summary.problems] == [
        ("web", ProblemKind.UNHEALTHY),
        ("worker", ProblemKind.EXITED),
    ]
    assert [u.name for u in summary.top] == ["api", "web"]
    assert summary.elsewhere.running == 2
    assert (summary.elsewhere.cpu_pct, summary.elsewhere.memory_bytes) == (3.0, 150 * MEBI)


async def test_all_scope_counts_every_container_as_the_projects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    _install_docker(
        tmp_path,
        monkeypatch,
        ps=[_ps("a1", "api", "running", "Up", PROJECT), _ps("e5", "other", "running", "Up", "/x")],
        stats=[
            _stats("a1", "api", "1%", "1MiB / 1GiB"),
            _stats("e5", "other", "2%", "1MiB / 1GiB"),
        ],
    )

    summary = (await _sampler(clock, None).sample()).summary

    assert summary is not None
    assert (summary.running, summary.elsewhere.running) == (2, 0)


@pytest.mark.parametrize(
    ("stats", "stats_status"),
    [
        ("not-json", 0),
        ("boom", 1),
        (HANG_SCRIPT, 0),
        (_stats("a1", "api", "1%", "lots / 1GiB"), 0),
    ],
)
async def test_failing_stats_keep_counts_and_problems_with_usage_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clock: FakeClock,
    stats: str,
    stats_status: int,
) -> None:
    _install_docker(
        tmp_path,
        monkeypatch,
        ps=[
            _ps("a1", "api", "running", "Up", PROJECT),
            _ps("c3", "worker", "restarting", "Restarting (1) 1s ago", PROJECT),
        ],
        stats=stats,
        stats_status=stats_status,
    )

    snapshot = await _sampler(clock, None, SHORT_TIMEOUT_SECONDS).sample()

    summary = snapshot.summary
    assert snapshot.available
    assert summary is not None
    assert not summary.usage_available
    assert (summary.running, summary.total) == (1, 2)
    assert [p.kind for p in summary.problems] == [ProblemKind.RESTARTING]
    assert (summary.cpu_pct, summary.memory_bytes, summary.top) == (None, None, ())


@pytest.mark.parametrize(
    ("ps", "ps_status"),
    [("not-json", 0), (HANG_SCRIPT, 0), ("daemon down", 1)],
)
async def test_ps_problems_are_failed_or_unavailable_and_never_invent_containers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: FakeClock, ps: str, ps_status: int
) -> None:
    _install_docker(tmp_path, monkeypatch, ps=ps, ps_status=ps_status, stats="")

    snapshot = await _sampler(clock, None, SHORT_TIMEOUT_SECONDS).sample()

    assert not snapshot.available
    assert snapshot.failed is not ps_status
    assert snapshot.error
    assert snapshot.summary is None


async def test_a_later_failure_keeps_the_last_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    _install_docker(
        tmp_path,
        monkeypatch,
        ps=[_ps("a1", "api", "running", "Up", PROJECT)],
        stats=[_stats("a1", "api", "1%", "1MiB / 1GiB")],
    )
    sampler = _sampler(clock, None)
    first = await sampler.sample()
    broken = tmp_path / "broken"
    broken.mkdir()
    _install_docker(broken, monkeypatch, ps="not-json", stats="")
    clock.advance(REFRESH_SECONDS)

    second = await sampler.sample_if_due()

    assert second.failed
    assert second.summary is first.summary
    assert second.sampled_at == first.sampled_at


async def test_a_shorter_refresh_cadence_resamples_on_the_next_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    _install_docker(tmp_path, monkeypatch, ps="", stats="")
    cadence = [LONG_REFRESH_SECONDS]
    sampler = DockerSampler(
        SubprocessRunner(),
        clock,
        refresh_seconds=lambda: cadence[0],
        timeout_seconds=TIMEOUT_SECONDS,
        project_roots=None,
    )
    first = await sampler.sample()
    clock.advance(SHORT_REFRESH_SECONDS)

    assert (await sampler.sample_if_due()).sampled_at == first.sampled_at

    cadence[0] = SHORT_REFRESH_SECONDS
    assert (await sampler.sample_if_due()).sampled_at == clock.now()


async def test_no_containers_is_an_empty_available_sample(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: FakeClock
) -> None:
    _install_docker(tmp_path, monkeypatch, ps="", stats="")

    snapshot = await _sampler(clock, [Path(PROJECT)]).sample()

    assert snapshot.available
    assert snapshot.summary is not None
    assert snapshot.summary.is_empty
