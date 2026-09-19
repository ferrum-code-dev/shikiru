"""Host and engine process-tree resource sampling for scheduling and the dashboard."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

import psutil

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence
    from datetime import datetime

    from shikiru.clock import Clock
    from shikiru.config.settings import ResourcesConfig

CPU_WINDOW_SECONDS = 30
RESOURCE_SAMPLE_SECONDS = 2
_BYTES_PER_GIB = 1024**3
_PERCENT = 100.0


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    """The latest host and engine process-tree measurements."""

    host_cpu_pct: float
    free_ram_gb: float
    instance_cpu_pct: float
    instance_rss_gb: float

    @property
    def resources_ok(self) -> bool:
        """Return whether all values are finite and non-negative."""
        return all(
            value >= 0
            for value in (
                self.host_cpu_pct,
                self.free_ram_gb,
                self.instance_cpu_pct,
                self.instance_rss_gb,
            )
        )


class ResourceSampler:
    """Samples host resources and the engine's process tree through `psutil`."""

    def __init__(
        self,
        clock: Clock,
        process_ids: Callable[[], Sequence[int]],
    ) -> None:
        """Use `clock` for the CPU window and `process_ids` for pane process roots."""
        self._clock = clock
        self._process_ids = process_ids
        self._host_cpu: deque[tuple[datetime, float]] = deque()
        self._instance_cpu: deque[tuple[datetime, float]] = deque()
        self._previous_instance: dict[int, tuple[float, datetime]] = {}
        self._last = ResourceSnapshot(0.0, 0.0, 0.0, 0.0)
        self._sampled_at: datetime | None = None

    @property
    def latest(self) -> ResourceSnapshot:
        """Return the most recent sample, or an empty sample before the first call."""
        return self._last

    def sample(self) -> ResourceSnapshot:
        """Take one host and instance process-tree sample."""
        now = self._clock.now()
        host_cpu = psutil.cpu_percent(interval=None)
        self._host_cpu.append((now, host_cpu))
        instance_cpu, instance_rss = self._instance_measurement(now)
        self._instance_cpu.append((now, instance_cpu))
        self._trim(now)
        self._last = ResourceSnapshot(
            host_cpu_pct=_average(value for _, value in self._host_cpu),
            free_ram_gb=psutil.virtual_memory().available / _BYTES_PER_GIB,
            instance_cpu_pct=_average(value for _, value in self._instance_cpu),
            instance_rss_gb=instance_rss,
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

    def _instance_measurement(self, now: datetime) -> tuple[float, float]:
        current: dict[int, tuple[float, datetime]] = {}
        rss = 0
        root_ids = {psutil.Process().pid, *self._process_ids()}
        for root_pid in root_ids:
            try:
                root = psutil.Process(root_pid)
                processes = (root, *root.children(recursive=True))
            except psutil.NoSuchProcess:
                continue
            except psutil.Error:
                raise
            for process in processes:
                try:
                    cpu = process.cpu_times()
                    memory = process.memory_info()
                except psutil.NoSuchProcess:
                    continue
                except psutil.Error:
                    raise
                current[process.pid] = (cpu.user + cpu.system, now)
                rss += memory.rss
        elapsed = (
            now - min((value[1] for value in self._previous_instance.values()), default=now)
        ).total_seconds()
        if elapsed <= 0:
            cpu_pct = 0.0
        else:
            delta = sum(
                current[pid][0] - previous[0]
                for pid, previous in self._previous_instance.items()
                if pid in current
            )
            cpu_pct = max(0.0, delta / elapsed / max(1, psutil.cpu_count() or 1) * _PERCENT)
        self._previous_instance = current
        return cpu_pct, rss / _BYTES_PER_GIB

    def _trim(self, now: datetime) -> None:
        cutoff = now.timestamp() - CPU_WINDOW_SECONDS
        while self._host_cpu and self._host_cpu[0][0].timestamp() < cutoff:
            self._host_cpu.popleft()
        while self._instance_cpu and self._instance_cpu[0][0].timestamp() < cutoff:
            self._instance_cpu.popleft()


def _average(values: Iterable[float]) -> float:
    sequence = tuple(values)
    return sum(sequence, 0.0) / len(sequence) if sequence else 0.0
