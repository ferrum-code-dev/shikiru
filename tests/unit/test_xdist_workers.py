"""Unit tests for the pure xdist worker-count rule."""

from __future__ import annotations

import pytest

from tests.support.xdist_workers import CPUS_PER_WORKER_ENV, DEFAULT_CPUS_PER_WORKER, worker_count


@pytest.mark.parametrize(
    ("cpus", "cpus_per_worker", "expected"),
    [
        pytest.param(10, None, 10 // DEFAULT_CPUS_PER_WORKER, id="default-halves-cpus"),
        pytest.param(10, "1", 10, id="override-one-worker-per-cpu"),
        pytest.param(1, None, 1, id="never-below-one-worker"),
        pytest.param(10, "abc", ValueError, id="non-integer-rejected"),
        pytest.param(10, "0", ValueError, id="zero-rejected"),
        pytest.param(10, "-2", ValueError, id="negative-rejected"),
    ],
)
def test_worker_count_follows_divisor_and_rejects_invalid_override(
    cpus: int, cpus_per_worker: str | None, expected: int | type[ValueError]
) -> None:
    if expected is ValueError:
        with pytest.raises(ValueError, match=CPUS_PER_WORKER_ENV):
            worker_count(cpus, cpus_per_worker)
    else:
        assert worker_count(cpus, cpus_per_worker) == expected
