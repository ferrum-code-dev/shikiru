from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from tests.support.clock import FakeClock
from tests.support.waiting import yield_control

pytestmark = pytest.mark.asyncio

_START = datetime(2026, 1, 1, tzinfo=UTC)


async def test_sleeper_wakes_only_after_advance_passes_its_deadline() -> None:
    clock = FakeClock(_START)
    task = asyncio.ensure_future(clock.sleep(5))

    await yield_control()
    clock.advance(3)
    await yield_control()
    assert not task.done()

    clock.advance(2)
    await yield_control()
    assert task.done()
