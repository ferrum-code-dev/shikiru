"""Docker container sampling for the dashboard."""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from shikiru.domain.docker import (
    ContainerUsage,
    DockerParseError,
    DockerSummary,
    parse_ps_record,
    parse_stats_record,
    summarize,
)
from shikiru.errors import CommandTimeoutError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import datetime
    from pathlib import PurePath
    from typing import Any

    from shikiru.clock import Clock
    from shikiru.process import ProcessResult, SubprocessRunner

DOCKER_BINARY = "docker"
DOCKER_PS_SUBCOMMAND = "ps"
DOCKER_ALL_FLAG = "-a"
DOCKER_STATS_SUBCOMMAND = "stats"
DOCKER_NO_STREAM_FLAG = "--no-stream"
DOCKER_FORMAT_FLAG = "--format"
DOCKER_JSON_FORMAT = "json"


@dataclass(frozen=True)
class DockerSnapshot:
    """The latest Docker sample and whether its daemon responded."""

    available: bool
    summary: DockerSummary | None = None
    sampled_at: datetime | None = None
    error: str | None = None
    failed: bool = False


class DockerSampler:
    """Samples `docker ps -a` and `docker stats --no-stream` together through `SubprocessRunner`."""

    def __init__(
        self,
        runner: SubprocessRunner,
        clock: Clock,
        *,
        refresh_seconds: Callable[[], float],
        timeout_seconds: float,
        project_roots: Sequence[PurePath] | None,
    ) -> None:
        """Use injected process and time boundaries; `project_roots=None` scopes every container."""
        self._runner = runner
        self._clock = clock
        self._refresh_seconds = refresh_seconds
        self._timeout_seconds = timeout_seconds
        self._project_roots = project_roots
        self._sampled_at: datetime | None = None
        self._last = DockerSnapshot(available=False)

    async def sample(self) -> DockerSnapshot:
        """Probe Docker and return the scoped container summary."""
        now = self._clock.now()
        if shutil.which(DOCKER_BINARY) is None:
            self._record_problem(error="Docker CLI is unavailable", failed=False)
        else:
            listing, stats = await asyncio.gather(
                self._attempt(DOCKER_PS_SUBCOMMAND, DOCKER_ALL_FLAG),
                self._attempt(DOCKER_STATS_SUBCOMMAND, DOCKER_NO_STREAM_FLAG),
            )
            self._record(now, listing, stats)
        self._sampled_at = now
        return self._last

    async def sample_if_due(self) -> DockerSnapshot:
        """Run a Docker sample no more often than the configured refresh cadence."""
        sampled_at = self._sampled_at
        if (
            sampled_at is None
            or (self._clock.now() - sampled_at).total_seconds() >= self._refresh_seconds()
        ):
            return await self.sample()
        return self._last

    async def _attempt(self, *arguments: str) -> ProcessResult | CommandTimeoutError | OSError:
        try:
            return await self._runner.run(
                [DOCKER_BINARY, *arguments, DOCKER_FORMAT_FLAG, DOCKER_JSON_FORMAT],
                timeout=self._timeout_seconds,
                check=False,
            )
        except (CommandTimeoutError, OSError) as error:
            return error

    def _record(
        self,
        now: datetime,
        listing: ProcessResult | CommandTimeoutError | OSError,
        stats: ProcessResult | CommandTimeoutError | OSError,
    ) -> None:
        if isinstance(listing, (CommandTimeoutError, OSError)):
            self._record_problem(error=str(listing), failed=True)
        elif listing.returncode != 0:
            error = listing.stderr.strip() or f"Docker ps exited with status {listing.returncode}"
            self._record_problem(error=error, failed=False)
        else:
            try:
                containers = _decode(listing.stdout, parse_ps_record)
                usages = _usages(stats)
            except DockerParseError as error:
                self._record_problem(error=str(error), failed=True)
                return
            self._last = DockerSnapshot(
                available=True,
                summary=summarize(containers, usages, self._project_roots),
                sampled_at=now,
            )

    def _record_problem(self, *, error: str, failed: bool) -> None:
        self._last = DockerSnapshot(
            available=False,
            summary=self._last.summary,
            sampled_at=self._last.sampled_at,
            error=error,
            failed=failed,
        )


def _usages(
    stats: ProcessResult | CommandTimeoutError | OSError,
) -> list[ContainerUsage] | None:
    if isinstance(stats, (CommandTimeoutError, OSError)) or stats.returncode != 0:
        return None
    try:
        parsed = _decode(stats.stdout, parse_stats_record)
    except DockerParseError:
        return None
    return [usage for usage in parsed if usage is not None]


def _decode[Parsed](stdout: str, parse: Callable[[dict[str, object]], Parsed]) -> list[Parsed]:
    return [parse(_record(line)) for line in stdout.splitlines() if line.strip()]


def _record(line: str) -> dict[str, object]:
    try:
        decoded = json.loads(line)
    except json.JSONDecodeError as error:
        message = f"docker returned invalid JSON: {error}"
        raise DockerParseError(message) from error
    if not isinstance(decoded, dict):
        message = "docker JSON record must be an object"
        raise DockerParseError(message)
    return cast("dict[str, Any]", decoded)
