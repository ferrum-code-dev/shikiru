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


async def test_concurrent_sleepers_wake_in_deadline_order() -> None:
    clock = FakeClock(_START)
    order: list[str] = []

    async def sleeper(name: str, seconds: float) -> None:
        await clock.sleep(seconds)
        order.append(name)

    tasks = [
        asyncio.ensure_future(sleeper("late", 5)),
        asyncio.ensure_future(sleeper("early", 2)),
        asyncio.ensure_future(sleeper("middle", 3)),
    ]
    await yield_control()

    clock.advance(5)
    await asyncio.gather(*tasks)

    assert order == ["early", "middle", "late"]


async def test_due_sleep_yields_so_a_concurrently_scheduled_task_runs_first() -> None:
    clock = FakeClock(_START)
    order: list[str] = []

    async def waits_on_due_sleep() -> None:
        await clock.sleep(0)
        order.append("waiter")

    async def runs_immediately() -> None:
        order.append("other")

    waiter = asyncio.ensure_future(waits_on_due_sleep())
    other = asyncio.ensure_future(runs_immediately())
    await asyncio.gather(waiter, other)

    assert order == ["other", "waiter"]


async def test_time_never_advances_on_its_own() -> None:
    clock = FakeClock(_START)
    task = asyncio.ensure_future(clock.sleep(1))

    for _ in range(5):
        await yield_control()

    assert clock.now() == _START
    assert not task.done()

    clock.advance(1)
    await yield_control()
    assert task.done()
