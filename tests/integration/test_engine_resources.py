"""Resource sampling through psutil for the engine and an isolated tmux pane."""

from __future__ import annotations

from shikiru.telemetry.resources import RESOURCE_SAMPLE_SECONDS, ResourceSampler
from tests.support.clock import FakeClock
from tests.support.factories import TASK_CREATED_AT


def test_resource_sampler_respects_the_two_second_sampling_cadence() -> None:
    clock = FakeClock(TASK_CREATED_AT)
    sampler = ResourceSampler(clock)

    first = sampler.sample_if_due()
    assert sampler.sample_if_due() is first
    clock.advance(RESOURCE_SAMPLE_SECONDS)
    assert sampler.sample_if_due() is not first
