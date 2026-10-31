from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from tests.support.clock import FakeClock
from tests.support.waiting import yield_control

START = datetime(2026, 1, 1, tzinfo=UTC)


async def test_advance_to_next_sleeper_wakes_each_earliest_deadline_in_turn() -> None:
    clock = FakeClock(START)
    woken: list[int] = []

    async def sleep_then_record(seconds: int) -> None:
        await clock.sleep(seconds)
        woken.append(seconds)

    sleepers = [asyncio.create_task(sleep_then_record(seconds)) for seconds in (30, 10, 20, 5)]
    await yield_control()
    sleepers[-1].cancel()
    with suppress(asyncio.CancelledError):
        await sleepers[-1]

    clock.advance_to_next_sleeper()
    await yield_control()
    assert clock.now() == START + timedelta(seconds=10)
    assert woken == [10]

    clock.advance_to_next_sleeper()
    await yield_control()
    assert clock.now() == START + timedelta(seconds=20)
    assert woken == [10, 20]

    clock.advance_to_next_sleeper()
    await yield_control()
    assert clock.now() == START + timedelta(seconds=30)
    assert woken == [10, 20, 30]

    clock.advance_to_next_sleeper()
    assert clock.now() == START + timedelta(seconds=30)
