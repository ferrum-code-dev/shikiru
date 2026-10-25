"""Pure Docker container parsing, scoping and ranking."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

DOCKER_TOP_COUNT = 5
PS_ID_FIELD = "ID"
PS_NAMES_FIELD = "Names"
PS_STATE_FIELD = "State"
PS_STATUS_FIELD = "Status"
PS_LABELS_FIELD = "Labels"
STATS_ID_FIELD = "ID"
STATS_NAME_FIELD = "Name"
STATS_CPU_FIELD = "CPUPerc"
STATS_MEMORY_FIELD = "MemUsage"
NOT_RUNNING_READING = "--"
COMPOSE_WORKING_DIR_LABEL = "com.docker.compose.project.working_dir"
STATE_RUNNING = "running"
STATE_RESTARTING = "restarting"
STATE_EXITED = "exited"
UNHEALTHY_MARKER = "(unhealthy)"
NAME_SEPARATOR = ","
LABEL_SEPARATOR = ","
LABEL_ASSIGNMENT = "="
MEMORY_SEPARATOR = "/"
PERCENT_SUFFIX = "%"
BYTES_PER_KILO = 1000
BYTES_PER_KIBI = 1024
BYTES_PER_MEBIBYTE = BYTES_PER_KIBI**2
MEMORY_UNIT_BYTES = {
    "B": 1,
    "kB": BYTES_PER_KILO,
    "KB": BYTES_PER_KILO,
    "MB": BYTES_PER_KILO**2,
    "GB": BYTES_PER_KILO**3,
    "TB": BYTES_PER_KILO**4,
    "KiB": BYTES_PER_KIBI,
    "MiB": BYTES_PER_MEBIBYTE,
    "GiB": BYTES_PER_KIBI**3,
    "TiB": BYTES_PER_KIBI**4,
}
_MEMORY_READING = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>[A-Za-z]+)")
_EXIT_STATUS = re.compile(r"Exited \((?P<code>\d+)\)")


class DockerParseError(ValueError):
    """A Docker record does not match the documented CLI output."""


class ProblemKind(StrEnum):
    """Why a container needs attention."""

    UNHEALTHY = "unhealthy"
    RESTARTING = "restarting"
    EXITED = "exited"


@dataclass(frozen=True)
class ContainerProblem:
    """One container that is unhealthy, restarting or exited non-zero."""

    name: str
    kind: ProblemKind
    exit_code: int | None = None


@dataclass(frozen=True)
class ContainerInfo:
    """One `docker ps -a` record reduced to the facts the dashboard uses."""

    id: str
    name: str
    running: bool
    problem: ContainerProblem | None
    working_dir: str | None


@dataclass(frozen=True)
class ContainerUsage:
    """One running container's CPU and memory as numbers."""

    id: str
    name: str
    cpu_pct: float
    memory_bytes: int


@dataclass(frozen=True)
class ElsewhereTotals:
    """Running containers outside the scope; usage is `None` when unavailable."""

    running: int
    cpu_pct: float | None
    memory_bytes: int | None


@dataclass(frozen=True)
class DockerSummary:
    """Scoped counts, usage totals, problems, the CPU ranking and the rest of the host."""

    running: int
    total: int
    cpu_pct: float | None
    memory_bytes: int | None
    problems: tuple[ContainerProblem, ...]
    top: tuple[ContainerUsage, ...]
    elsewhere: ElsewhereTotals
    usage_available: bool

    @property
    def is_empty(self) -> bool:
        """Whether the host has no scoped container and no running one elsewhere."""
        return not self.total and not self.elsewhere.running


def parse_cpu_percent(text: str) -> float | None:
    """Return the percentage in `1.23%`, or `None` for the not-running reading."""
    stripped = text.strip()
    if stripped == NOT_RUNNING_READING:
        return None
    try:
        return float(stripped.removesuffix(PERCENT_SUFFIX))
    except ValueError as error:
        message = f"docker CPU reading is not a percentage: {text!r}"
        raise DockerParseError(message) from error


def parse_memory_usage(text: str) -> int | None:
    """Return the used bytes in `12MiB / 1.9GiB`, or `None` for the not-running reading."""
    used = text.partition(MEMORY_SEPARATOR)[0].strip()
    if used == NOT_RUNNING_READING:
        return None
    match = _MEMORY_READING.fullmatch(used)
    if match is None or match["unit"] not in MEMORY_UNIT_BYTES:
        message = f"docker memory reading is not a size: {text!r}"
        raise DockerParseError(message)
    return round(float(match["value"]) * MEMORY_UNIT_BYTES[match["unit"]])


def parse_labels(text: str) -> dict[str, str]:
    """Return the `k=v,k=v` label string as a mapping; fragments without `=` are skipped."""
    labels: dict[str, str] = {}
    for fragment in text.split(LABEL_SEPARATOR):
        key, assignment, value = fragment.partition(LABEL_ASSIGNMENT)
        if assignment:
            labels[key] = value
    return labels


def parse_ps_record(record: Mapping[str, object]) -> ContainerInfo:
    """Return a `docker ps -a --format json` record as a `ContainerInfo`."""
    name = _text(record, PS_NAMES_FIELD).split(NAME_SEPARATOR)[0]
    state = _text(record, PS_STATE_FIELD)
    status = _text(record, PS_STATUS_FIELD)
    working_dir = parse_labels(_text(record, PS_LABELS_FIELD)).get(COMPOSE_WORKING_DIR_LABEL)
    return ContainerInfo(
        id=_text(record, PS_ID_FIELD),
        name=name,
        running=state == STATE_RUNNING,
        problem=_problem(name, state, status),
        working_dir=working_dir,
    )


def parse_stats_record(record: Mapping[str, object]) -> ContainerUsage | None:
    """Return a `docker stats` record as numbers, or `None` while it has no reading yet."""
    cpu_pct = parse_cpu_percent(_text(record, STATS_CPU_FIELD))
    memory_bytes = parse_memory_usage(_text(record, STATS_MEMORY_FIELD))
    if cpu_pct is None or memory_bytes is None:
        return None
    return ContainerUsage(
        id=_text(record, STATS_ID_FIELD),
        name=_text(record, STATS_NAME_FIELD),
        cpu_pct=cpu_pct,
        memory_bytes=memory_bytes,
    )


def rank_by_cpu(usages: Sequence[ContainerUsage], limit: int) -> tuple[ContainerUsage, ...]:
    """Return the `limit` busiest containers: CPU descending, then memory descending, then name."""
    ranked = sorted(usages, key=lambda usage: (-usage.cpu_pct, -usage.memory_bytes, usage.name))
    return tuple(ranked[:limit])


def in_project(info: ContainerInfo, project_roots: Sequence[PurePath]) -> bool:
    """Return whether the container's compose working directory is inside a project root."""
    if info.working_dir is None:
        return False
    working_dir = PurePath(info.working_dir)
    return any(working_dir.is_relative_to(root) for root in project_roots)


def summarize(
    containers: Sequence[ContainerInfo],
    usages: Sequence[ContainerUsage] | None,
    project_roots: Sequence[PurePath] | None,
) -> DockerSummary:
    """Summarize the scoped containers; `None` usages mean unavailable, `None` roots mean all."""
    scoped = [c for c in containers if project_roots is None or in_project(c, project_roots)]
    scoped_ids = {c.id for c in scoped}
    other_running = [c for c in containers if c.running and c.id not in scoped_ids]
    problems = tuple(c.problem for c in scoped if c.problem is not None)
    running = [c for c in scoped if c.running]
    if usages is None:
        return DockerSummary(
            running=len(running),
            total=len(scoped),
            cpu_pct=None,
            memory_bytes=None,
            problems=problems,
            top=(),
            elsewhere=ElsewhereTotals(len(other_running), None, None),
            usage_available=False,
        )
    usage_by_id = {usage.id: usage for usage in usages}
    scoped_usage = [usage_by_id[c.id] for c in running if c.id in usage_by_id]
    other_usage = [usage_by_id[c.id] for c in other_running if c.id in usage_by_id]
    return DockerSummary(
        running=len(running),
        total=len(scoped),
        cpu_pct=sum(usage.cpu_pct for usage in scoped_usage),
        memory_bytes=sum(usage.memory_bytes for usage in scoped_usage),
        problems=problems,
        top=rank_by_cpu(scoped_usage, DOCKER_TOP_COUNT),
        elsewhere=ElsewhereTotals(
            len(other_running),
            sum(usage.cpu_pct for usage in other_usage),
            sum(usage.memory_bytes for usage in other_usage),
        ),
        usage_available=True,
    )


def _text(record: Mapping[str, object], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str):
        message = f"docker record field {field} is missing or not a string"
        raise DockerParseError(message)
    return value


def _problem(name: str, state: str, status: str) -> ContainerProblem | None:
    if state == STATE_RESTARTING:
        return ContainerProblem(name, ProblemKind.RESTARTING)
    if state == STATE_RUNNING and UNHEALTHY_MARKER in status:
        return ContainerProblem(name, ProblemKind.UNHEALTHY)
    if state != STATE_EXITED:
        return None
    match = _EXIT_STATUS.match(status)
    if match is None:
        message = f"docker exited status has no exit code: {status!r}"
        raise DockerParseError(message)
    code = int(match["code"])
    return ContainerProblem(name, ProblemKind.EXITED, code) if code != 0 else None
