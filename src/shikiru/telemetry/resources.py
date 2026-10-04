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

_BYTES_PER_GIB = 1024**3


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    """The latest host and optional Shikiru process-tree measurements."""

    host_cpu_pct: float
    host_ram_used_pct: float
    free_ram_gb: float
    instance_cpu_pct: float | None = None
    instance_rss_gb: float | None = None
    sampled_at: datetime | None = None
    host_ram_used_bytes: int | None = None
    host_ram_total_bytes: int | None = None


class ResourceSampleError(RuntimeError):
    """The operating system could not provide a complete host or process sample."""


class ResourceSampler:
    """Samples host CPU and available memory through `psutil`."""

    def __init__(self, clock: Clock, config: ResourcesConfig) -> None:
        """Use `clock` and configured cadences for resource samples."""
        self._clock = clock
        self._config = config
        self._host_cpu: deque[tuple[datetime, float]] = deque()
        self._last = ResourceSnapshot(0.0, 0.0, 0.0)
        self._last_complete = ResourceSnapshot(0.0, 0.0, 0.0)
        self._sampled_at: datetime | None = None
        self._instance_sampled_at: datetime | None = None
        self._processes: dict[tuple[int, float], psutil.Process] = {}

    def sample(self) -> ResourceSnapshot:
        """Take one host CPU and available memory sample."""
        now = self._clock.now()
        try:
            host_cpu = psutil.cpu_percent(interval=None)
            virtual_memory = psutil.virtual_memory()
        except psutil.Error as error:
            raise ResourceSampleError(str(error)) from error
        self._host_cpu.append((now, host_cpu))
        self._trim(now)
        self._last = ResourceSnapshot(
            host_cpu_pct=_average(value for _, value in self._host_cpu),
            host_ram_used_pct=virtual_memory.percent,
            free_ram_gb=virtual_memory.available / _BYTES_PER_GIB,
            sampled_at=now,
            host_ram_used_bytes=virtual_memory.used,
            host_ram_total_bytes=virtual_memory.total,
        )
        self._sampled_at = now
        return self._last

    def sample_if_due(self) -> ResourceSnapshot:
        """Sample at the configured cadence and return the latest snapshot."""
        sampled_at = self._sampled_at
        if (
            sampled_at is None
            or (self._clock.now() - sampled_at).total_seconds() >= self._config.sample_seconds
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

    def sample_process_trees(
        self, engine_pid: int | None, pane_pids: Iterable[int]
    ) -> ResourceSnapshot:
        """Sample host usage and the selected engine and pane process trees."""
        now = self._clock.now()
        sampled_at = self._instance_sampled_at
        if (
            sampled_at is not None
            and (now - sampled_at).total_seconds() < self._config.sample_seconds
        ):
            return self._last_complete
        host_sample = self.sample_if_due()
        processes = self._process_tree(engine_pid, pane_pids)
        measurements = [
            measurement
            for process in processes
            if (measurement := self._measure(process)) is not None
        ]
        instance_cpu_pct: float | None = None
        instance_rss_gb: float | None = None
        if measurements:
            cpu_count = psutil.cpu_count(logical=True)
            if cpu_count is None or cpu_count < 1:
                message = "psutil did not report a logical CPU count"
                raise RuntimeError(message)
            instance_cpu_pct = sum(cpu for cpu, _ in measurements) / cpu_count
            rss_bytes = sum(rss for _, rss in measurements)
            instance_rss_gb = rss_bytes / _BYTES_PER_GIB
        snapshot = ResourceSnapshot(
            host_cpu_pct=host_sample.host_cpu_pct,
            host_ram_used_pct=host_sample.host_ram_used_pct,
            free_ram_gb=host_sample.free_ram_gb,
            instance_cpu_pct=instance_cpu_pct,
            instance_rss_gb=instance_rss_gb,
            sampled_at=now,
            host_ram_used_bytes=host_sample.host_ram_used_bytes,
            host_ram_total_bytes=host_sample.host_ram_total_bytes,
        )
        self._last = snapshot
        self._last_complete = snapshot
        self._instance_sampled_at = now
        return snapshot

    def latest(self) -> ResourceSnapshot:
        """Return the latest sample without starting a new observation."""
        return self._last_complete

    def _process_tree(
        self, engine_pid: int | None, pane_pids: Iterable[int]
    ) -> list[psutil.Process]:
        roots = set(pane_pids)
        if engine_pid is not None:
            roots.add(engine_pid)
        found: dict[tuple[int, float], psutil.Process] = {}
        for pid in roots:
            try:
                root = psutil.Process(pid)
                descendants = [root, *root.children(recursive=True)]
            except psutil.NoSuchProcess, psutil.ZombieProcess:
                continue
            except psutil.Error as error:
                raise ResourceSampleError(str(error)) from error
            for process in descendants:
                try:
                    identity = (process.pid, process.create_time())
                except psutil.NoSuchProcess, psutil.ZombieProcess:
                    continue
                except psutil.Error as error:
                    raise ResourceSampleError(str(error)) from error
                found[identity] = self._processes.get(identity, process)
        self._processes = found
        return list(found.values())

    @staticmethod
    def _measure(process: psutil.Process) -> tuple[float, int] | None:
        try:
            return process.cpu_percent(interval=None), process.memory_info().rss
        except psutil.NoSuchProcess, psutil.ZombieProcess:
            return None
        except psutil.Error as error:
            raise ResourceSampleError(str(error)) from error

    def _trim(self, now: datetime) -> None:
        cutoff = now.timestamp() - self._config.cpu_window_seconds
        while self._host_cpu and self._host_cpu[0][0].timestamp() < cutoff:
            self._host_cpu.popleft()


def _average(values: Iterable[float]) -> float:
    sequence = tuple(values)
    return sum(sequence, 0.0) / len(sequence) if sequence else 0.0
