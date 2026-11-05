from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from tests.support.clock import FakeClock
from tests.support.waiting import wait_until_advancing, yield_control

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
    assert not clock.has_pending_sleep()


async def test_cancelled_sleeper_does_not_keep_clock_pending() -> None:
    clock = FakeClock(START)
    sleeper = asyncio.create_task(clock.sleep(10))
    await yield_control()
    sleeper.cancel()
    assert not clock.has_pending_sleep()
    with suppress(asyncio.CancelledError):
        await sleeper
    clock.advance_to_next_sleeper()
    assert clock.now() == START


async def test_completed_condition_does_not_advance_unrelated_sleeper() -> None:
    clock = FakeClock(START)
    sleeper = asyncio.create_task(clock.sleep(10))
    await yield_control()
    await wait_until_advancing(clock, lambda: True, timeout=1, jump_while=lambda: True)
    assert clock.now() == START
    assert not sleeper.done()
    sleeper.cancel()
    with suppress(asyncio.CancelledError):
        await sleeper


async def test_jump_waits_while_real_work_is_outstanding_then_reaches_the_sleeper() -> None:
    clock = FakeClock(START)
    idle = False
    woken = asyncio.Event()

    async def sleep_then_wake() -> None:
        await clock.sleep(10)
        woken.set()

    sleeper = asyncio.create_task(sleep_then_wake())
    await yield_control()
    polls = 0

    def became_idle() -> bool:
        nonlocal polls, idle
        polls += 1
        idle = polls > 3
        return woken.is_set()

    await wait_until_advancing(clock, became_idle, timeout=5, jump_while=lambda: idle)
    assert clock.now() == START + timedelta(seconds=10)
    await sleeper
