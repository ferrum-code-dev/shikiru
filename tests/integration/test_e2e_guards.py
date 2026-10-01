from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from functools import partial
from typing import TYPE_CHECKING

import pytest

from shikiru.clock import SystemClock
from shikiru.domain.events import Event, EventKind
from shikiru.errors import ShikiruError
from shikiru.paths import TURNS_DIR_NAME, engine_log_path, run_dir, state_db_path
from shikiru.process import SubprocessRunner
from shikiru.store.database import Database
from shikiru.store.events import EventLog
from tests.e2e.budgets import (
    ATTEMPT_BUDGETS,
    INVOCATION_BUDGET,
    InvocationBudget,
    InvocationBudgetExhausted,
)
from tests.e2e.diagnostics import (
    RERUN_OUTSIDE_WORKSPACE,
    RERUN_SKIPPED_RULE,
    ShikiruFailure,
    Snapshot,
    rerun_reason,
    take_snapshot,
)
from tests.e2e.watchdog import StallDetector, StallError, guarded
from tests.support.clock import FakeClock
from tests.support.factories import TASK_CREATED_AT
from tests.support.git_repo import commit_all, init_repo
from tests.support.store import seed_task
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

STALL_AFTER = timedelta(minutes=4)
_RUNNER = SubprocessRunner()


async def _project(root: Path) -> Path:
    await init_repo(_RUNNER, root)
    (root / "README.md").write_text("base\n")
    await commit_all(_RUNNER, root, "initial")
    state_db_path(root).parent.mkdir(parents=True)
    return root


def _seed(root: Path, *messages: str) -> None:
    connection = Database(state_db_path(root)).connect()
    try:
        seed_task(connection, SystemClock(), title="the task")
        events = EventLog(connection, SystemClock())
        for message in messages:
            events.append(EventKind.NUDGE, task_id="T-1", message=message)
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("steps", "stalled"),
    [
        ([(240, 0, False)], True),
        ([(239, 0, False)], False),
        ([(200, 0, False), (200, 1, False)], False),
        ([(200, 0, False), (200, 0, True), (200, 0, False)], False),
        ([(200, 0, False), (40, 0, False)], True),
    ],
)
def test_a_run_stalls_only_after_the_window_without_events_or_turn_process(
    steps: list[tuple[int, int, bool]], stalled: bool
) -> None:
    clock = FakeClock(TASK_CREATED_AT)
    detector = StallDetector(clock, STALL_AFTER)
    verdict = False
    for seconds, last_event_id, turn_alive in steps:
        clock.advance(seconds)
        verdict = detector.stalled(last_event_id, turn_alive=turn_alive)
    assert verdict is stalled


async def test_a_stalled_attempt_fails_fast_naming_the_last_event_the_tasks_and_the_log(
    tmp_path: Path,
) -> None:
    root = await _project(tmp_path / "project")
    _seed(root, "first nudge", "second nudge")
    engine_log_path(root).parent.mkdir(parents=True, exist_ok=True)
    engine_log_path(root).write_text("engine line one\nengine line two\n")
    (root / "README.md").write_text("an agent edited this\n")
    clock = FakeClock(TASK_CREATED_AT)

    async def never_finishes() -> None:
        await asyncio.Event().wait()

    probe, polls = _counted(lambda: take_snapshot(root, _RUNNER, clock))
    guard = asyncio.ensure_future(
        guarded(
            never_finishes(),
            probe=probe,
            clock=clock,
            timeout=ATTEMPT_BUDGETS["e3"].total_seconds(),
            stall_after=STALL_AFTER,
        )
    )
    with pytest.raises(StallError) as caught:
        await _drive(clock, guard, polls)

    text = str(caught.value)
    assert "second nudge" in text.split("recent events:", maxsplit=1)[0]
    assert "T-1 status=" in text
    assert "engine line two" in text
    assert "README.md" in text
    assert clock.now() - TASK_CREATED_AT < ATTEMPT_BUDGETS["e3"]


async def test_a_sandbox_denial_in_an_agent_transcript_is_found_in_the_snapshot(
    tmp_path: Path,
) -> None:
    root = await _project(tmp_path / "project")
    clean = await take_snapshot(root, _RUNNER, FakeClock(TASK_CREATED_AT))
    stream = run_dir(root, "T-1") / TURNS_DIR_NAME / "01-implement" / "implementer.stream.jsonl"
    stream.parent.mkdir(parents=True)
    stream.write_text('{"output": "Unable to create index.lock: Operation not permitted"}\n')

    denied = await take_snapshot(root, _RUNNER, FakeClock(TASK_CREATED_AT))

    assert clean.sandbox_denial is None
    assert denied.sandbox_denial == "Operation not permitted"


async def test_an_attempt_that_outlives_its_budget_fails_with_diagnostics(tmp_path: Path) -> None:
    root = await _project(tmp_path / "project")
    _seed(root, "only nudge")
    clock = FakeClock(TASK_CREATED_AT)

    async def keeps_a_turn_running() -> None:
        await asyncio.Event().wait()

    async def busy_probe() -> Snapshot:
        return replace(await take_snapshot(root, _RUNNER, clock), turn_alive=True)

    probe, polls = _counted(busy_probe)
    guard = asyncio.ensure_future(
        guarded(
            keeps_a_turn_running(),
            probe=probe,
            clock=clock,
            timeout=600,
            stall_after=STALL_AFTER,
        )
    )
    with pytest.raises(TimeoutError, match="budget"):
        await _drive(clock, guard, polls)


async def test_a_finished_attempt_returns_without_waiting_for_its_budget(tmp_path: Path) -> None:
    root = await _project(tmp_path / "project")
    clock = FakeClock(TASK_CREATED_AT)

    async def done() -> None:
        return None

    await guarded(
        done(),
        probe=lambda: take_snapshot(root, _RUNNER, clock),
        clock=clock,
        timeout=600,
        stall_after=STALL_AFTER,
    )


def _counted(
    probe: Callable[[], Awaitable[Snapshot]],
) -> tuple[Callable[[], Awaitable[Snapshot]], list[int]]:
    polls: list[int] = []

    async def counted() -> Snapshot:
        snapshot = await probe()
        polls.append(1)
        return snapshot

    return counted, polls


def _polled_past(guard: asyncio.Future[None], polls: list[int], before: int) -> bool:
    return guard.done() or len(polls) > before


async def _drive(clock: FakeClock, guard: asyncio.Future[None], polls: list[int]) -> None:
    await wait_until(clock.has_pending_sleep, timeout=5)
    while not guard.done():
        before = len(polls)
        clock.advance(60)
        await wait_until(partial(_polled_past, guard, polls, before), timeout=5)
    await guard


def _snapshot(
    *kinds: EventKind, checkout_status: str = "", sandbox_denial: str | None = None
) -> Snapshot:
    return Snapshot(
        events=tuple(
            Event(id=index, ts=TASK_CREATED_AT, kind=kind, message=kind.value)
            for index, kind in enumerate(kinds, start=1)
        ),
        tasks=(),
        checkout_status=checkout_status,
        engine_log_tail="",
        turn_alive=False,
        sandbox_denial=sandbox_denial,
    )


@pytest.mark.parametrize(
    ("error", "snapshot", "expected"),
    [
        (AssertionError("A1 was not ticked"), _snapshot(), RERUN_SKIPPED_RULE),
        (AssertionError("x"), _snapshot(EventKind.INTEGRATION_WAITING), RERUN_OUTSIDE_WORKSPACE),
        (
            TimeoutError("budget"),
            _snapshot(EventKind.INTEGRATION_WAITING),
            RERUN_OUTSIDE_WORKSPACE,
        ),
        (AssertionError("x"), _snapshot(checkout_status=" M README.md\n"), RERUN_OUTSIDE_WORKSPACE),
        (StallError("no progress"), _snapshot(), None),
        (TimeoutError("budget"), _snapshot(), None),
        (AssertionError("x"), _snapshot(EventKind.ENGINE_FAULT), None),
        (
            AssertionError("A1 was not ticked"),
            _snapshot(sandbox_denial="Operation not permitted"),
            None,
        ),
        (
            AssertionError("x"),
            _snapshot(EventKind.INTEGRATION_WAITING, sandbox_denial="was denied permission"),
            None,
        ),
        (
            AssertionError("x"),
            _snapshot(EventKind.ENGINE_FAULT, EventKind.INTEGRATION_WAITING),
            None,
        ),
        (ShikiruFailure("engine stopped"), _snapshot(EventKind.INTEGRATION_WAITING), None),
        (ShikiruError("start failed"), _snapshot(), None),
        (OSError("disk"), _snapshot(), None),
    ],
)
def test_only_agent_flakiness_earns_the_one_rerun(
    error: Exception, snapshot: Snapshot, expected: str | None
) -> None:
    assert rerun_reason(error, snapshot) == expected


def test_every_attempt_is_cut_to_what_the_invocation_has_left() -> None:
    clock = FakeClock(TASK_CREATED_AT)
    budget = InvocationBudget(clock, INVOCATION_BUDGET)
    assert budget.attempt_timeout_seconds("e3") == ATTEMPT_BUDGETS["e3"].total_seconds()

    clock.advance((INVOCATION_BUDGET - timedelta(minutes=3)).total_seconds())
    assert budget.attempt_timeout_seconds("e3") == timedelta(minutes=3).total_seconds()

    clock.advance(timedelta(minutes=3).total_seconds())
    with pytest.raises(InvocationBudgetExhausted):
        budget.attempt_timeout_seconds("e1")
