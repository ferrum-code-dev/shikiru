"""Docker container sampling for the dashboard (docs/design/06-operator.md §5.4)."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from shikiru.errors import CommandTimeoutError

if TYPE_CHECKING:
    from datetime import datetime
    from typing import Any

    from shikiru.clock import Clock
    from shikiru.process import SubprocessRunner

DOCKER_BINARY = "docker"
DOCKER_STATS_SUBCOMMAND = "stats"
DOCKER_NO_STREAM_FLAG = "--no-stream"
DOCKER_FORMAT_FLAG = "--format"
DOCKER_JSON_FORMAT = "json"
DOCKER_NAME_FIELD = "Name"
DOCKER_CPU_FIELD = "CPUPerc"
DOCKER_MEMORY_FIELD = "MemUsage"


@dataclass(frozen=True)
class DockerContainer:
    """One Docker container's CPU and memory readings."""

    name: str
    cpu_pct: str
    memory: str


@dataclass(frozen=True)
class DockerSnapshot:
    """The latest Docker sample and whether its daemon responded."""

    available: bool
    containers: tuple[DockerContainer, ...] = ()


class DockerStatsError(ValueError):
    """Docker returned a stats record that does not match its JSON protocol."""


class DockerSampler:
    """Samples `docker stats --no-stream --format json` through `SubprocessRunner`."""

    def __init__(
        self,
        runner: SubprocessRunner,
        clock: Clock,
        *,
        refresh_seconds: float,
        timeout_seconds: float,
    ) -> None:
        """Use injected process and time boundaries for Docker sampling."""
        self._runner = runner
        self._clock = clock
        self._refresh_seconds = refresh_seconds
        self._timeout_seconds = timeout_seconds
        self._sampled_at: datetime | None = None
        self._last = DockerSnapshot(available=False)

    async def sample(self) -> DockerSnapshot:
        """Probe Docker and return its current container CPU and memory readings."""
        now = self._clock.now()
        if shutil.which(DOCKER_BINARY) is None:
            self._last = DockerSnapshot(available=False)
        else:
            try:
                result = await self._runner.run(
                    [
                        DOCKER_BINARY,
                        DOCKER_STATS_SUBCOMMAND,
                        DOCKER_NO_STREAM_FLAG,
                        DOCKER_FORMAT_FLAG,
                        DOCKER_JSON_FORMAT,
                    ],
                    timeout=self._timeout_seconds,
                    check=False,
                )
            except CommandTimeoutError:
                self._last = DockerSnapshot(available=False)
            else:
                if result.returncode == 0:
                    self._last = DockerSnapshot(
                        available=True,
                        containers=tuple(
                            _parse_container(line) for line in result.stdout.splitlines()
                        ),
                    )
                else:
                    self._last = DockerSnapshot(available=False)
        self._sampled_at = now
        return self._last

    async def sample_if_due(self) -> DockerSnapshot:
        """Run a Docker sample no more often than the configured refresh cadence."""
        sampled_at = self._sampled_at
        if (
            sampled_at is None
            or (self._clock.now() - sampled_at).total_seconds() >= self._refresh_seconds
        ):
            return await self.sample()
        return self._last


def _parse_container(line: str) -> DockerContainer:
    try:
        decoded = json.loads(line)
    except json.JSONDecodeError as error:
        message = f"docker stats returned invalid JSON: {error}"
        raise DockerStatsError(message) from error
    if not isinstance(decoded, dict):
        message = "docker stats JSON record must be an object"
        raise DockerStatsError(message)
    record = cast("dict[str, Any]", decoded)
    try:
        name = record[DOCKER_NAME_FIELD]
        cpu_pct = record[DOCKER_CPU_FIELD]
        memory = record[DOCKER_MEMORY_FIELD]
    except KeyError as error:
        message = f"docker stats record is missing {error.args[0]}"
        raise DockerStatsError(message) from error
    if not all(isinstance(value, str) for value in (name, cpu_pct, memory)):
        message = "docker stats name, CPU and memory fields must be strings"
        raise DockerStatsError(message)
    return DockerContainer(name=name, cpu_pct=cpu_pct, memory=memory)
