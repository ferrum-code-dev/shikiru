"""The only place tests are allowed to poll, and the one place their deadlines are scaled."""

from __future__ import annotations

import asyncio
import functools
import inspect
import os
import subprocess
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import TYPE_CHECKING, cast

from textual.css.query import NoMatches

from tests.support.spawn_hook import processes_in_flight

if TYPE_CHECKING:
    from tests.support.clock import FakeClock

TIME_SCALE_ENV = "SHIKIRU_TEST_TIME_SCALE"
MAXIMUM_TIME_SCALE = 8.0
_MINIMUM_TIME_SCALE = 1.0
_CALIBRATION_SPAWNS = 20
_CALIBRATION_UNLOADED_SECONDS = 0.025
_CALIBRATION_COMMAND = ["true"]
_POLL_INTERVAL_SECONDS = 0.02
_NOT_SAMPLED = object()

SHORT_WAIT_SECONDS = 5.0
MEDIUM_WAIT_SECONDS = 10.0
UI_WAIT_SECONDS = 30
PROCESS_WAIT_SECONDS = 30
LONG_WAIT_SECONDS = 60.0
ACTION_CONFIRMATION_SECONDS = 60
ACTION_CONFIRMATION_EXPIRY_SECONDS = 5
ENGINE_STOP_SECONDS = 30.0

_Predicate = Callable[[], bool | Awaitable[bool]]


class WaitTimeoutError(TimeoutError):
    """A `wait_until` predicate never became true within its timeout."""


@functools.cache
def time_scale() -> float:
    """Return the factor every test deadline is multiplied by, once per test process.

    `SHIKIRU_TEST_TIME_SCALE` sets it; unset, it is measured from a short batch of spawns.
    """
    text = os.environ.get(TIME_SCALE_ENV)
    if text is None:
        return scale_from_calibration(_measure_calibration_batch())
    scale = float(text)
    if scale <= 0:
        message = f"{TIME_SCALE_ENV} must be positive, got {text!r}"
        raise ValueError(message)
    return scale


def scale_from_calibration(batch_seconds: float) -> float:
    """Return the factor for a calibration batch that took `batch_seconds`.

    The factor is the batch time over its unloaded time, never below one and never above
    `MAXIMUM_TIME_SCALE`.
    """
    ratio = batch_seconds / _CALIBRATION_UNLOADED_SECONDS
    return min(max(ratio, _MINIMUM_TIME_SCALE), MAXIMUM_TIME_SCALE)


def scaled(seconds: float) -> float:
    """Return `seconds` multiplied by the test time scale."""
    return seconds * time_scale()


def _measure_calibration_batch() -> float:
    start = time.perf_counter()
    for _ in range(_CALIBRATION_SPAWNS):
        subprocess.run(_CALIBRATION_COMMAND, check=True, capture_output=True)  # noqa: S603 # keep: fixed no-op spawn, not product code
    return time.perf_counter() - start


async def yield_control() -> None:
    """Let one pass of the event loop run, without waiting for any duration."""
    await asyncio.sleep(0)


async def wait_until(predicate: _Predicate, timeout: float) -> None:
    """Poll `predicate` (sync or async) until it is true, or raise `WaitTimeoutError`.

    `timeout` is scaled by `time_scale()`. A Textual `NoMatches` raised by the predicate means its
    widget is not mounted yet; it counts as "not yet" and, if the wait times out, is chained as the
    cause.
    """
    await _wait(functools.partial(_sample, predicate), bool, timeout, _describe(predicate))


async def wait_for[T](
    sample: Callable[[], T | Awaitable[T]], accept: Callable[[T], bool], *, timeout: float
) -> T:
    """Poll `sample` (sync or async) until `accept` is true of its value, and return that value.

    Assert on the returned value, not on a fresh sample: it is the one that satisfied the condition.
    On timeout the message carries the last sampled value, so a pane capture shows the screen that
    failed. `timeout` is scaled by `time_scale()`, and `NoMatches` counts as "not yet" as in
    `wait_until`.
    """
    return await _wait(functools.partial(_sample, sample), accept, timeout, _describe(sample))


async def wait_until_advancing(
    clock: FakeClock,
    predicate: _Predicate,
    timeout: float,
    *,
    step_seconds: float = 1.0,
    jump_while: Callable[[], bool] | None = None,
) -> None:
    """Like `wait_until`, moving the fake clock whenever a task is sleeping on it.

    The clock moves `step_seconds` per poll. With `jump_while` it moves straight to the next
    pending deadline instead, but only while `jump_while()` is true and no subprocess call is in
    flight: the caller states that no real work is outstanding, so the earliest sleeper is the
    wait under test and no long timeout can fire early. While either is false the clock stays put.
    """

    async def advanced() -> bool:
        result = await _sample(predicate)
        if result:
            return result
        if jump_while is not None:
            if jump_while() and processes_in_flight() == 0:
                clock.advance_to_next_sleeper()
        elif clock.has_pending_sleep():
            clock.advance(step_seconds)
        return await _sample(predicate)

    await _wait(advanced, bool, timeout, _describe(predicate))


async def stop_engine(task: asyncio.Task[object], clock: FakeClock) -> None:
    """Cancel an engine or flow task and advance the fake clock until the task has ended.

    Its teardown may sleep on the fake clock, which only moves when advanced, so a plain `await`
    after `cancel()` can hang. The wait is bounded by `ENGINE_STOP_SECONDS`; the task's own
    cancellation is absorbed, and any other error it raised propagates.
    """
    task.cancel()
    await wait_until_advancing(clock, task.done, timeout=ENGINE_STOP_SECONDS)
    with suppress(asyncio.CancelledError):
        await task


async def _sample[T](sample: Callable[[], T | Awaitable[T]]) -> T:
    result = sample()
    if inspect.isawaitable(result):
        return await result
    return cast("T", result)


async def _wait[T](
    sample: Callable[[], Awaitable[T]],
    accept: Callable[[T], bool],
    timeout: float,
    description: str,
) -> T:
    limit = scaled(timeout)
    deadline = time.monotonic() + limit
    not_mounted: NoMatches | None = None
    last: object = _NOT_SAMPLED
    while True:
        try:
            value = await sample()
        except NoMatches as error:
            not_mounted = error
        else:
            last = value
            if accept(value):
                return value
        if time.monotonic() >= deadline:
            raise WaitTimeoutError(_timeout_message(description, limit, last)) from not_mounted
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)


def _describe(predicate: Callable[[], object]) -> str:
    return getattr(predicate, "__qualname__", None) or repr(predicate)


def _timeout_message(description: str, limit: float, last: object) -> str:
    message = f"{description} did not become true within {limit}s"
    if last is _NOT_SAMPLED:
        return message
    return f"{message}; last value {last!r}"
