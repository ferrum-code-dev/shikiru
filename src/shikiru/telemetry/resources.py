"""Host and engine process-tree resource sampling for scheduling and the dashboard."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

import psutil

from shikiru.process import ProcessIdentity

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from datetime import datetime

    from shikiru.clock import Clock
    from shikiru.config.settings import ResourcesConfig

_BYTES_PER_GIB = 1024**3
_PPID_ATTR = "ppid"
_CREATE_TIME_ATTR = "create_time"
_PERCENT_PER_CORE = 100.0
_MIN_LIFETIME_SECONDS = 0.1


@dataclass(frozen=True, slots=True)
class _TableEntry:
    process: psutil.Process
    ppid: int
    create_time: float


@dataclass(frozen=True, slots=True)
class _ProcessTable:
    entries: dict[int, _TableEntry]
    children: dict[int, list[int]]


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    """The latest host, Shikiru and agent process-tree measurements."""

    host_cpu_pct: float
    host_ram_used_pct: float
    free_ram_gb: float
    instance_cpu_pct: float | None = None
    instance_rss_gb: float | None = None
    sampled_at: datetime | None = None
    host_ram_used_bytes: int | None = None
    host_ram_total_bytes: int | None = None
    shikiru_cpu_pct: float | None = None
    shikiru_rss_gb: float | None = None
    agents_cpu_pct: float | None = None
    agents_rss_gb: float | None = None
    shikiru_process_tree_empty: bool | None = None
    agents_process_tree_empty: bool | None = None


@dataclass(frozen=True, slots=True)
class ResourceProcessRoots:
    """Classified live process identities supplied by the current project."""

    shikiru: tuple[ProcessIdentity, ...]
    agents: tuple[ProcessIdentity, ...]


class ResourceSampleError(RuntimeError):
    """The operating system could not provide a complete host or process sample."""


class ResourceSampler:
    """Samples host CPU and available memory through `psutil`."""

    def __init__(self, clock: Clock, config: Callable[[], ResourcesConfig]) -> None:
        """Use `clock` and the cadences `config` returns when each sample is due."""
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
            or (self._clock.now() - sampled_at).total_seconds() >= self._config().sample_seconds
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

    def process_sample_is_fresh(self) -> bool:
        """Return whether the latest process-tree sample is younger than the sample cadence."""
        sampled_at = self._instance_sampled_at
        return (
            sampled_at is not None
            and (self._clock.now() - sampled_at).total_seconds() < self._config().sample_seconds
        )

    def sample_process_roots(self, roots: ResourceProcessRoots) -> ResourceSnapshot:
        """Sample host, Shikiru and agent trees from classified process identities."""
        if self.process_sample_is_fresh():
            return self._last_complete
        now = self._clock.now()
        host_sample = self.sample_if_due()
        table = self._process_table()
        agent_processes = self._process_tree(roots.agents, table)
        agent_identities = set(agent_processes)
        shikiru_processes = {
            identity: process
            for identity, process in self._process_tree(roots.shikiru, table).items()
            if identity not in agent_identities
        }
        all_processes = {**shikiru_processes, **agent_processes}
        measurements = self._measure_processes(all_processes, now.timestamp())
        shikiru_measurements = [
            measurement
            for identity, measurement in measurements.items()
            if identity in shikiru_processes
        ]
        agent_measurements = [
            measurement
            for identity, measurement in measurements.items()
            if identity in agent_processes
        ]
        instance_cpu_pct, instance_rss_gb = _usage(list(measurements.values()))
        shikiru_cpu_pct, shikiru_rss_gb = _usage(shikiru_measurements)
        agents_cpu_pct, agents_rss_gb = _usage(agent_measurements)
        snapshot = ResourceSnapshot(
            host_cpu_pct=host_sample.host_cpu_pct,
            host_ram_used_pct=host_sample.host_ram_used_pct,
            free_ram_gb=host_sample.free_ram_gb,
            instance_cpu_pct=instance_cpu_pct,
            instance_rss_gb=instance_rss_gb,
            sampled_at=now,
            host_ram_used_bytes=host_sample.host_ram_used_bytes,
            host_ram_total_bytes=host_sample.host_ram_total_bytes,
            shikiru_cpu_pct=shikiru_cpu_pct,
            shikiru_rss_gb=shikiru_rss_gb,
            agents_cpu_pct=agents_cpu_pct,
            agents_rss_gb=agents_rss_gb,
            shikiru_process_tree_empty=not shikiru_measurements,
            agents_process_tree_empty=not agent_measurements,
        )
        self._last = snapshot
        self._last_complete = snapshot
        self._instance_sampled_at = now
        self._processes = {
            identity: process
            for identity, process in all_processes.items()
            if identity in measurements
        }
        return snapshot

    def latest(self) -> ResourceSnapshot:
        """Return the latest sample without starting a new observation."""
        return self._last_complete

    def _process_table(self) -> _ProcessTable:
        entries: dict[int, _TableEntry] = {}
        children: dict[int, list[int]] = {}
        for process in psutil.process_iter([_PPID_ATTR, _CREATE_TIME_ATTR]):
            ppid = process.info[_PPID_ATTR]
            create_time = process.info[_CREATE_TIME_ATTR]
            if ppid is None or create_time is None:
                continue
            entries[process.pid] = _TableEntry(process, ppid, create_time)
            children.setdefault(ppid, []).append(process.pid)
        return _ProcessTable(entries, children)

    @staticmethod
    def _add_unlisted_root(root_identity: ProcessIdentity, table: _ProcessTable) -> bool:
        try:
            process = psutil.Process(root_identity.pid)
            entry = _TableEntry(process, process.ppid(), process.create_time())
        except psutil.NoSuchProcess, psutil.ZombieProcess:
            return False
        except psutil.Error as error:
            raise ResourceSampleError(str(error)) from error
        table.entries[root_identity.pid] = entry
        table.children.setdefault(entry.ppid, []).append(root_identity.pid)
        return True

    def _process_tree(
        self, roots: Iterable[ProcessIdentity], table: _ProcessTable
    ) -> dict[tuple[int, float], psutil.Process]:
        found: dict[tuple[int, float], psutil.Process] = {}
        for root_identity in roots:
            if root_identity.pid not in table.entries and not self._add_unlisted_root(
                root_identity, table
            ):
                continue
            root = table.entries[root_identity.pid]
            if not root_identity.matches(ProcessIdentity(root_identity.pid, root.create_time)):
                continue
            pending = [root_identity.pid]
            while pending:
                pid = pending.pop()
                entry = table.entries[pid]
                process_key = (pid, entry.create_time)
                if process_key in found:
                    continue
                found[process_key] = self._processes.get(process_key, entry.process)
                pending.extend(
                    child
                    for child in table.children.get(pid, ())
                    if table.entries[child].create_time >= entry.create_time
                )
        return found

    def _measure_processes(
        self,
        processes: dict[tuple[int, float], psutil.Process],
        now_timestamp: float,
    ) -> dict[tuple[int, float], tuple[float, int]]:
        measurements: dict[tuple[int, float], tuple[float, int]] = {}
        for identity, process in processes.items():
            first_sight_at = None if identity in self._processes else now_timestamp
            measurement = self._measure(process, first_sight_at)
            if measurement is not None:
                measurements[identity] = measurement
        return measurements

    @staticmethod
    def _measure(process: psutil.Process, first_sight_at: float | None) -> tuple[float, int] | None:
        try:
            with process.oneshot():
                cpu_pct = process.cpu_percent(interval=None)
                if first_sight_at is not None:
                    cpu_pct = _lifetime_cpu_pct(process, first_sight_at)
                return cpu_pct, process.memory_info().rss
        except psutil.NoSuchProcess, psutil.ZombieProcess:
            return None
        except psutil.Error as error:
            raise ResourceSampleError(str(error)) from error

    def _trim(self, now: datetime) -> None:
        cutoff = now.timestamp() - self._config().cpu_window_seconds
        while self._host_cpu and self._host_cpu[0][0].timestamp() < cutoff:
            self._host_cpu.popleft()


def _lifetime_cpu_pct(process: psutil.Process, now_timestamp: float) -> float:
    times = process.cpu_times()
    lifetime = max(now_timestamp - process.create_time(), _MIN_LIFETIME_SECONDS)
    return (times.user + times.system) * _PERCENT_PER_CORE / lifetime


def _average(values: Iterable[float]) -> float:
    sequence = tuple(values)
    return sum(sequence, 0.0) / len(sequence) if sequence else 0.0


def _usage(measurements: list[tuple[float, int]]) -> tuple[float | None, float | None]:
    if not measurements:
        return None, None
    cpu_count = psutil.cpu_count(logical=True)
    if cpu_count is None or cpu_count < 1:
        message = "psutil did not report a logical CPU count"
        raise RuntimeError(message)
    cpu_pct = sum(cpu for cpu, _ in measurements) / cpu_count
    rss_gb = sum(rss for _, rss in measurements) / _BYTES_PER_GIB
    return cpu_pct, rss_gb
