"""Host and engine process-tree resource sampling for scheduling and the dashboard."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

import psutil

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    from shikiru.clock import Clock
    from shikiru.config.settings import ResourcesConfig

CPU_WINDOW_SECONDS = 30
RESOURCE_SAMPLE_SECONDS = 2
_BYTES_PER_GIB = 1024**3


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    """The latest host CPU and available memory measurements."""

    host_cpu_pct: float
    free_ram_gb: float


class ResourceSampler:
    """Samples host CPU and available memory through `psutil`."""

    def __init__(self, clock: Clock) -> None:
        """Use `clock` to keep host CPU samples within one rolling window."""
        self._clock = clock
        self._host_cpu: deque[tuple[datetime, float]] = deque()
        self._last = ResourceSnapshot(0.0, 0.0)
        self._sampled_at: datetime | None = None

    def sample(self) -> ResourceSnapshot:
        """Take one host CPU and available memory sample."""
        now = self._clock.now()
        host_cpu = psutil.cpu_percent(interval=None)
        self._host_cpu.append((now, host_cpu))
        self._trim(now)
        self._last = ResourceSnapshot(
            host_cpu_pct=_average(value for _, value in self._host_cpu),
            free_ram_gb=psutil.virtual_memory().available / _BYTES_PER_GIB,
        )
        self._sampled_at = now
        return self._last

    def sample_if_due(self) -> ResourceSnapshot:
        """Sample at the protocol cadence and return the latest snapshot."""
        sampled_at = self._sampled_at
        if (
            sampled_at is None
            or (self._clock.now() - sampled_at).total_seconds() >= RESOURCE_SAMPLE_SECONDS
        ):
            return self.sample()
        return self._last

    def resources_ok(self, config: ResourcesConfig) -> bool:
        """Return whether a fresh sample passes scheduling thresholds."""
        sample = self.sample_if_due()
        return (
            sample.host_cpu_pct < config.max_cpu_pct
            and sample.free_ram_gb >= config.min_free_ram_gb
        )

    def _trim(self, now: datetime) -> None:
        cutoff = now.timestamp() - CPU_WINDOW_SECONDS
        while self._host_cpu and self._host_cpu[0][0].timestamp() < cutoff:
            self._host_cpu.popleft()


def _average(values: Iterable[float]) -> float:
    sequence = tuple(values)
    return sum(sequence, 0.0) / len(sequence) if sequence else 0.0
