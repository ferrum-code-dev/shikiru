"""Resource sampling through psutil for the engine and an isolated tmux pane."""

from __future__ import annotations

from shikiru.telemetry.resources import ResourceSampler
from tests.support.clock import FakeClock
from tests.support.factories import TASK_CREATED_AT, make_settings


def test_resource_sampler_respects_the_configured_sampling_cadence() -> None:
    clock = FakeClock(TASK_CREATED_AT)
    settings = make_settings()
    resources = settings.orchestration.resources.model_copy(update={"sample_seconds": 3})
    sampler = ResourceSampler(clock, lambda: resources)

    first = sampler.sample_if_due()
    assert sampler.sample_if_due() is first
    clock.advance(resources.sample_seconds)
    assert sampler.sample_if_due() is not first
