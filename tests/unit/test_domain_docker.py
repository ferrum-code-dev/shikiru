from __future__ import annotations

from pathlib import PurePath
from typing import TYPE_CHECKING, Any

import pytest

from shikiru.domain.docker import (
    COMPOSE_WORKING_DIR_LABEL,
    ContainerInfo,
    ContainerProblem,
    ContainerUsage,
    DockerParseError,
    ProblemKind,
    in_project,
    parse_cpu_percent,
    parse_memory_usage,
    parse_ps_record,
    parse_stats_record,
    rank_by_cpu,
    summarize,
)

if TYPE_CHECKING:
    from collections.abc import Callable

KIBI = 1024
MEBI = KIBI**2
GIBI = KIBI**3
_PS_FIELDS = ("ID", "Names", "State", "Status", "Labels")


@pytest.mark.parametrize(
    ("text", "expected"),
    [("1.23%", 1.23), ("0.00%", 0.0), ("250%", 250.0), ("--", None)],
)
def test_cpu_percent_is_numeric_and_not_running_has_no_reading(
    text: str, expected: float | None
) -> None:
    assert parse_cpu_percent(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("12MiB / 1.9GiB", 12 * MEBI),
        ("1.5GiB / 4GiB", int(1.5 * GIBI)),
        ("7B / 1GiB", 7),
        ("3kB / 1GB", 3000),
        ("2MB / 1GB", 2_000_000),
        ("1GB / 2GB", 1_000_000_000),
        ("-- / --", None),
    ],
)
def test_memory_usage_reads_the_used_part_in_bytes(text: str, expected: int | None) -> None:
    assert parse_memory_usage(text) == expected


def _ps(state: str, status: str, labels: str = "", names: str = "web") -> dict[str, str]:
    return {"ID": "abc", "Names": names, "State": state, "Status": status, "Labels": labels}


@pytest.mark.parametrize(
    ("record", "expected", "running"),
    [
        (_ps("running", "Up 3 minutes"), None, True),
        (_ps("running", "Up 3 minutes (healthy)"), None, True),
        (_ps("running", "Up 3 minutes (unhealthy)"), ProblemKind.UNHEALTHY, True),
        (_ps("restarting", "Restarting (1) 5 seconds ago"), ProblemKind.RESTARTING, False),
        (_ps("exited", "Exited (1) 2m ago"), ProblemKind.EXITED, False),
        (_ps("exited", "Exited (0) 2m ago"), None, False),
        (_ps("created", "Created"), None, False),
        (_ps("paused", "Up 3 minutes (Paused)"), None, False),
    ],
)
def test_problems_are_unhealthy_restarting_or_exited_non_zero(
    record: dict[str, str], expected: ProblemKind | None, running: bool
) -> None:
    info = parse_ps_record(record)
    assert (info.problem.kind if info.problem else None) is expected
    assert info.running is running


def test_exited_problem_carries_the_exit_code() -> None:
    problem = parse_ps_record(_ps("exited", "Exited (137) 2m ago", names="worker")).problem
    assert problem == ContainerProblem("worker", ProblemKind.EXITED, 137)


def test_ps_record_reads_the_first_name_and_the_compose_working_directory() -> None:
    labels = f"a=b,{COMPOSE_WORKING_DIR_LABEL}=/work/app,note=x,y"
    info = parse_ps_record(_ps("running", "Up", labels, names="db,alias"))
    assert (info.name, info.working_dir) == ("db", "/work/app")
    assert parse_ps_record(_ps("running", "Up", "a=b")).working_dir is None


def _ps_without(field: str) -> dict[str, str]:
    record = _ps("running", "Up")
    del record[field]
    return record


@pytest.mark.parametrize(
    ("parser", "argument"),
    [
        (parse_cpu_percent, "fast"),
        (parse_cpu_percent, ""),
        (parse_cpu_percent, "1.2 percent"),
        (parse_memory_usage, "12 / 1GiB"),
        (parse_memory_usage, "12XiB / 1GiB"),
        (parse_memory_usage, "lots / 1GiB"),
        (parse_memory_usage, ""),
        (parse_ps_record, _ps("exited", "Exited")),
        *((parse_ps_record, _ps_without(field)) for field in _PS_FIELDS),
    ],
)
def test_malformed_docker_input_raises_a_parse_error(
    parser: Callable[[Any], object], argument: Any
) -> None:
    with pytest.raises(DockerParseError):
        parser(argument)


def test_stats_record_without_a_reading_yet_is_skipped() -> None:
    assert (
        parse_stats_record({"ID": "a", "Name": "n", "CPUPerc": "--", "MemUsage": "-- / --"}) is None
    )
    usage = parse_stats_record(
        {"ID": "a", "Name": "n", "CPUPerc": "2.5%", "MemUsage": "1MiB / 2GiB"}
    )
    assert usage == ContainerUsage("a", "n", 2.5, MEBI)


def _usage(name: str, cpu: float, memory: int) -> ContainerUsage:
    return ContainerUsage(name, name, cpu, memory)


def test_ranking_is_cpu_then_memory_then_name_and_capped() -> None:
    usages = [
        _usage("b", 5.0, 10),
        _usage("a", 5.0, 10),
        _usage("c", 5.0, 20),
        _usage("busy", 90.0, 1),
        _usage("idle", 0.0, 99),
    ]
    assert [u.name for u in rank_by_cpu(usages, 4)] == ["busy", "c", "a", "b"]


def _info(identity: str, working_dir: str | None, *, running: bool = True) -> ContainerInfo:
    return ContainerInfo(identity, identity, running, None, working_dir)


@pytest.mark.parametrize(
    ("working_dir", "expected"),
    [
        ("/work/app", True),
        ("/work/app/.shikiru/worktrees/T-1/service", True),
        ("/work/app2", False),
        ("/other", False),
        (None, False),
    ],
)
def test_project_containers_are_those_under_a_project_root(
    working_dir: str | None, expected: bool
) -> None:
    assert in_project(_info("x", working_dir), [PurePath("/work/app")]) is expected


def test_summary_splits_project_from_elsewhere_and_sums_scoped_usage() -> None:
    roots = [PurePath("/work/app")]
    containers = [
        _info("api", "/work/app"),
        _info("db", "/work/app/svc", running=False),
        _info("far", "/other"),
        _info("bare", None),
    ]
    usages = [_usage("api", 10.0, 100), _usage("far", 4.0, 40), _usage("bare", 1.0, 5)]

    summary = summarize(containers, usages, roots)

    assert (summary.running, summary.total) == (1, 2)
    assert (summary.cpu_pct, summary.memory_bytes) == (10.0, 100)
    assert [u.name for u in summary.top] == ["api"]
    assert (summary.elsewhere.running, summary.elsewhere.cpu_pct) == (2, 5.0)
    assert summary.elsewhere.memory_bytes == 45
    assert summary.usage_available


def test_all_scope_leaves_nothing_elsewhere() -> None:
    summary = summarize([_info("far", "/other"), _info("bare", None)], [], None)
    assert (summary.running, summary.total, summary.elsewhere.running) == (2, 2, 0)


def test_running_container_without_a_stats_row_is_tolerated() -> None:
    summary = summarize([_info("gone", None), _info("api", None)], [_usage("api", 3.0, 7)], None)
    assert (summary.running, summary.cpu_pct, [u.name for u in summary.top]) == (2, 3.0, ["api"])


def test_unavailable_usage_keeps_counts_and_problems_without_inventing_numbers() -> None:
    sick = ContainerInfo("s", "s", True, ContainerProblem("s", ProblemKind.UNHEALTHY), None)
    summary = summarize([sick, _info("ok", None)], None, None)
    assert (summary.running, summary.total, summary.problems) == (2, 2, (sick.problem,))
    assert (summary.cpu_pct, summary.memory_bytes, summary.top) == (None, None, ())
    assert not summary.usage_available


def test_summary_is_empty_only_without_scoped_or_elsewhere_running_containers() -> None:
    assert summarize([], [], None).is_empty
    assert not summarize([_info("far", "/other")], [], [PurePath("/work")]).is_empty
