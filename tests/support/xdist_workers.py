"""The `-n auto` worker-count rule for the test suite."""

from __future__ import annotations

CPUS_PER_WORKER_ENV = "SHIKIRU_TEST_CPUS_PER_WORKER"
DEFAULT_CPUS_PER_WORKER = 2
_MINIMUM_WORKERS = 1
_MINIMUM_CPUS_PER_WORKER = 1


def worker_count(cpus: int, cpus_per_worker: str | None) -> int:
    """Return the `-n auto` worker count: `cpus` over the divisor, or the default when None."""
    return max(_MINIMUM_WORKERS, cpus // _cpus_per_worker(cpus_per_worker))


def _cpus_per_worker(text: str | None) -> int:
    if text is None:
        return DEFAULT_CPUS_PER_WORKER
    try:
        divisor = int(text)
    except ValueError as error:
        message = f"{CPUS_PER_WORKER_ENV} must be an integer, got {text!r}"
        raise ValueError(message) from error
    if divisor < _MINIMUM_CPUS_PER_WORKER:
        message = f"{CPUS_PER_WORKER_ENV} must be at least {_MINIMUM_CPUS_PER_WORKER}, got {text!r}"
        raise ValueError(message)
    return divisor
