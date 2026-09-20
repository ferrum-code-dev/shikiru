"""Command handlers against the real task, event and escalation stores."""

from __future__ import annotations

import asyncio
import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from shikiru.agents.contracts import Rung, role_dir
from shikiru.agents.sdk.hooks import HookRecorder
from shikiru.clock import SystemClock
from shikiru.domain.commands import (
    AcceptResolution,
    AnswerResolution,
    Cancel,
    CancelResolution,
    Pause,
    Prioritize,
    Resolve,
    Resume,
    Retry,
    RetryResolution,
    Say,
    SetRung,
    Stop,
    Submit,
)
from shikiru.domain.escalation import EscalationStatus
from shikiru.domain.events import EventKind
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import Counters, EscalationReason, Hold, Mode, Role, Status, Step, Task
from shikiru.engine.escalations import EscalationRaiser
from shikiru.engine.handlers import (
    CancelHandler,
    HandlerContext,
    HandlerRegistry,
    PauseHandler,
    PrioritizeHandler,
    ResolveHandler,
    ResumeHandler,
    RetryHandler,
    SayHandler,
    SetRungHandler,
    StopHandler,
    SubmitHandler,
)
from shikiru.errors import CommandRejectedError
from shikiru.store.commands import CommandQueue
from shikiru.store.escalations import EscalationStore
from shikiru.store.events import EventLog
from shikiru.store.messages import MessageStore
from shikiru.workspace.roots import run_dir
from tests.support.flow import build_flow_task as build_task_with_panes
from tests.support.flow import init_project_repo
from tests.support.waiting import wait_until

if TYPE_CHECKING:
    from shikiru.clock import Clock
    from shikiru.store.runs import RunStore
    from shikiru.store.tasks import TaskStore
    from tests.support.engine import EngineEnv
    from tests.support.store import Connection

_FAKE_RUNG = Rung(agent="fake", model="fake-1", effort=None)
_SECOND_RUNG = Rung(agent="fake", model="fake-2", effort=None)


@dataclass
class HandlerFixture:
    registry: HandlerRegistry
    context: HandlerContext
    tasks: TaskStore
    escalations: EscalationStore
    events: EventLog
    runs: RunStore
    clock: Clock
    connection: Connection
    cancelled: list[str]
    started: list[str]
    finished: list[str]
    consult_questions: dict[str, str]
    stopped: list[bool]


@pytest.fixture
def handler_env(engine_env: EngineEnv) -> HandlerFixture:
    clock = SystemClock()
    tasks = engine_env.tasks
    events = EventLog(engine_env.connection, clock)
    escalations = EscalationStore(engine_env.connection, clock)
    runs = engine_env.runs
    cancelled: list[str] = []
    started: list[str] = []
    finished: list[str] = []
    consult_questions: dict[str, str] = {}
    stopped: list[bool] = []

    async def cancel_task(task_id: str) -> None:
        cancelled.append(task_id)

    async def start_task(task_id: str) -> None:
        started.append(task_id)

    async def finish_task(task: Task) -> None:
        finished.append(task.id)

    def set_consult_question(task_id: str, question: str) -> None:
        consult_questions[task_id] = question

    def stop_engine() -> None:
        stopped.append(True)

    async def refresh_titles(_task: Task) -> None:
        return

    context = HandlerContext(
        tasks=tasks,
        escalations=escalations,
        events=events,
        messages=MessageStore(engine_env.connection),
        sessions=engine_env.manager,
        project_root=engine_env.project_root,
        cancel_task=cancel_task,
        start_task=start_task,
        finish_task=finish_task,
        set_consult_question=set_consult_question,
        stop_engine=stop_engine,
        refresh_titles=refresh_titles,
    )
    registry = HandlerRegistry(
        [
            SubmitHandler(context),
            PrioritizeHandler(context),
            PauseHandler(context),
            ResumeHandler(context),
            CancelHandler(context),
            RetryHandler(context),
            SetRungHandler(context),
            SayHandler(context),
            ResolveHandler(context),
            StopHandler(context),
        ]
    )
    return HandlerFixture(
        registry,
        context,
        tasks,
        escalations,
        events,
        runs,
        clock,
        engine_env.connection,
        cancelled,
        started,
        finished,
        consult_questions,
        stopped,
    )


def _queued(
    environment: HandlerFixture, title: str = "task", depends_on: list[str] | None = None
) -> Task:
    queued = CommandQueue(environment.connection, environment.clock).append(
        Submit(
            task_id="",
            title=title,
            request=title,
            leader=_FAKE_RUNG,
            implementer=_FAKE_RUNG,
            depends_on=depends_on or [],
        )
    )
    assert isinstance(queued.command, Submit)
    return environment.tasks.insert_from_submit(queued.command)


def _running(environment: HandlerFixture, *, title: str = "task") -> Task:
    task = _queued(environment, title)
    environment.tasks.apply_transition(
        task.id, Transition.stay(task, step=Step.IMPLEMENT, status=Status.RUNNING)
    )
    environment.tasks.set_slot(task.id, 1)
    current = environment.tasks.get(task.id)
    assert current is not None
    return current


async def _escalated(
    environment: HandlerFixture,
    *,
    reason: EscalationReason,
    step: Step,
    counters: Counters | None = None,
    depends_on: list[str] | None = None,
) -> tuple[Task, str]:
    task = _queued(environment, reason.value, depends_on)
    transition = Transition.stay(task, step=step, status=Status.ESCALATED)
    if counters is not None:
        transition = dataclasses.replace(transition, counters=counters)
    environment.tasks.apply_transition(task.id, transition)
    environment.tasks.set_slot(task.id, 1)
    raiser = EscalationRaiser(
        environment.escalations,
        environment.events,
        environment.runs,
        environment.context.project_root,
    )
    escalation = await raiser.raise_escalation(
        task,
        step=step,
        reason=reason,
        question="What should happen next?",
        attempted="The handler test raised an escalation.",
        reports=(),
        facts="",
        pane_tail="",
    )
    return task, escalation.id


async def test_handlers_cover_submit_priority_pause_resume_rung_and_stop(
    handler_env: HandlerFixture,
) -> None:
    queued = CommandQueue(handler_env.connection, handler_env.clock).append(
        Submit(task_id="", title="queue me", request="queue me")
    )
    assert isinstance(queued.command, Submit)

    submitted = await handler_env.registry.handle(queued.command)
    task_id = str(submitted["task_id"])
    assert handler_env.tasks.get(task_id) is not None

    await handler_env.registry.handle(Prioritize(task_id=task_id, priority=8))
    await handler_env.registry.handle(Pause(task_id=task_id))
    paused = handler_env.tasks.get(task_id)
    assert paused is not None
    assert Hold.PAUSED in paused.holds

    await handler_env.registry.handle(Resume(task_id=task_id))
    rung = Rung(agent="fake", model="fake-3", effort=None)
    await handler_env.registry.handle(SetRung(task_id=task_id, role=Role.IMPLEMENTER, rung=rung))
    await handler_env.registry.handle(Stop())

    current = handler_env.tasks.get(task_id)
    assert current is not None
    assert current.priority == 8
    assert not current.holds
    assert current.overrides[Role.IMPLEMENTER] == rung
    assert handler_env.stopped == [True]


async def test_pause_now_resume_and_cancel_follow_task_lifecycle(
    handler_env: HandlerFixture,
) -> None:
    task = _running(handler_env)

    await handler_env.registry.handle(Pause(task_id=task.id, now=True))
    paused = handler_env.tasks.get(task.id)
    assert paused is not None
    assert paused.status is Status.RUNNING
    assert Hold.PAUSED in paused.holds
    assert handler_env.cancelled == [task.id]

    await handler_env.registry.handle(Resume(task_id=task.id))
    assert handler_env.started == [task.id]

    await handler_env.registry.handle(Cancel(task_id=task.id))
    cancelled = handler_env.tasks.get(task.id)
    assert cancelled is not None
    assert cancelled.status is Status.CANCELLED
    assert cancelled.slot is None
    assert handler_env.finished == [task.id]
    assert handler_env.events.since(0, kinds=[EventKind.TASK_CANCELLED], task_id=task.id)


async def test_prioritize_rejects_a_running_task(handler_env: HandlerFixture) -> None:
    task = _running(handler_env)

    with pytest.raises(CommandRejectedError):
        await handler_env.registry.handle(Prioritize(task_id=task.id, priority=10))


async def test_retry_requeues_current_step_and_preserves_previous_slot(
    handler_env: HandlerFixture,
) -> None:
    task = _running(handler_env)
    counters = Counters(rejections_on_rung=2, consults=3, conflict_rounds=4)
    transition = Transition.stay(task, step=Step.IMPLEMENT, status=Status.RUNNING)
    handler_env.tasks.apply_transition(task.id, dataclasses.replace(transition, counters=counters))

    await handler_env.registry.handle(Retry(task_id=task.id))

    retried = handler_env.tasks.get(task.id)
    assert retried is not None
    assert retried.status is Status.QUEUED
    assert retried.step is Step.IMPLEMENT
    assert retried.slot == 1
    assert retried.counters == Counters(rejections_on_rung=2)
    assert handler_env.cancelled == [task.id]


async def test_say_writes_a_message_and_injects_the_live_agent(
    handler_env: HandlerFixture, engine_env: EngineEnv
) -> None:
    await init_project_repo(engine_env.project_root)
    task = await build_task_with_panes(engine_env, mode=Mode.IMPLEMENT)
    handler_env.tasks.apply_transition(
        task.id, Transition.stay(task, step=Step.IMPLEMENT, status=Status.RUNNING)
    )
    current = handler_env.tasks.get(task.id)
    assert current is not None
    await engine_env.manager.ensure(current, Role.IMPLEMENTER)

    result = await handler_env.registry.handle(
        Say(task_id=task.id, role=Role.IMPLEMENTER, text="please inspect the edge case")
    )

    path = Path(str(result["path"]))
    assert await asyncio.to_thread(path.read_text) == "please inspect the edge case"
    assert handler_env.events.since(0, kinds=[EventKind.OPERATOR_MESSAGE], task_id=task.id)

    run = engine_env.runs.get_live(task.id, Role.IMPLEMENTER)
    assert run is not None
    hooks = role_dir(run_dir(engine_env.project_root, task.id), Role.IMPLEMENTER.value)
    expected_line = f"Operator message: read {path} and take it into account."

    async def received() -> bool:
        entries = await asyncio.to_thread(lambda: tuple(HookRecorder(hooks).tail()))
        return any(entry.value.get("text") == expected_line for entry in entries)

    await wait_until(received, timeout=10)


async def test_answer_resolution_queues_a_consult_turn(
    handler_env: HandlerFixture,
) -> None:
    task, escalation_id = await _escalated(
        handler_env,
        reason=EscalationReason.DECISION_REQUIRED,
        step=Step.IMPLEMENT,
    )

    await handler_env.registry.handle(
        Resolve(
            escalation_id=escalation_id,
            resolution=AnswerResolution(text="use the safer parser"),
        )
    )

    updated = handler_env.tasks.get(task.id)
    assert updated is not None
    assert updated.step is Step.CONSULT
    assert updated.status is Status.QUEUED
    assert updated.slot == 1
    assert handler_env.consult_questions == {task.id: "use the safer parser"}
    assert handler_env.cancelled == []
    resolved = handler_env.escalations.get(escalation_id)
    assert resolved is not None
    assert resolved.status is EscalationStatus.RESOLVED


async def test_accept_resolution_queues_integration(
    handler_env: HandlerFixture,
) -> None:
    task, escalation_id = await _escalated(
        handler_env, reason=EscalationReason.ITERATIONS_EXHAUSTED, step=Step.VERIFY
    )

    await handler_env.registry.handle(
        Resolve(escalation_id=escalation_id, resolution=AcceptResolution())
    )

    updated = handler_env.tasks.get(task.id)
    assert updated is not None
    assert updated.step is Step.INTEGRATE
    assert updated.status is Status.QUEUED
    assert updated.slot == 1
    assert handler_env.cancelled == [task.id]


async def test_retry_resolution_keeps_step_and_applies_rung_override(
    handler_env: HandlerFixture,
) -> None:
    task, escalation_id = await _escalated(
        handler_env, reason=EscalationReason.AGENT_ERROR, step=Step.IMPLEMENT
    )

    await handler_env.registry.handle(
        Resolve(
            escalation_id=escalation_id,
            resolution=RetryResolution(rung=_SECOND_RUNG),
        )
    )

    updated = handler_env.tasks.get(task.id)
    assert updated is not None
    assert updated.step is Step.IMPLEMENT
    assert updated.status is Status.QUEUED
    assert updated.overrides[Role.IMPLEMENTER] == _SECOND_RUNG


async def test_dependency_cancelled_retry_drops_only_cancelled_dependencies(
    handler_env: HandlerFixture,
) -> None:
    cancelled_dependency = _queued(handler_env, "cancelled dependency")
    retained_dependency = _queued(handler_env, "retained dependency")
    cancelled_state = handler_env.tasks.get(cancelled_dependency.id)
    retained_state = handler_env.tasks.get(retained_dependency.id)
    assert cancelled_state is not None
    assert retained_state is not None
    handler_env.tasks.apply_transition(
        cancelled_state.id,
        Transition.stay(cancelled_state, step=Step.IMPLEMENT, status=Status.CANCELLED),
    )
    handler_env.tasks.apply_transition(
        retained_state.id,
        Transition.stay(retained_state, step=Step.IMPLEMENT, status=Status.DONE),
    )
    task, escalation_id = await _escalated(
        handler_env,
        reason=EscalationReason.DEPENDENCY_CANCELLED,
        step=Step.IMPLEMENT,
        depends_on=[cancelled_dependency.id, retained_dependency.id],
    )

    await handler_env.registry.handle(
        Resolve(escalation_id=escalation_id, resolution=RetryResolution())
    )

    updated = handler_env.tasks.get(task.id)
    assert updated is not None
    assert updated.depends_on == [retained_dependency.id]


async def test_cancel_resolution_marks_task_cancelled_and_finishes_boundary(
    handler_env: HandlerFixture,
) -> None:
    task, escalation_id = await _escalated(
        handler_env, reason=EscalationReason.DECISION_REQUIRED, step=Step.IMPLEMENT
    )

    await handler_env.registry.handle(
        Resolve(escalation_id=escalation_id, resolution=CancelResolution())
    )

    cancelled = handler_env.tasks.get(task.id)
    assert cancelled is not None
    assert cancelled.status is Status.CANCELLED
    assert cancelled.slot is None
    assert handler_env.finished == [task.id]
    assert handler_env.events.since(0, kinds=[EventKind.TASK_CANCELLED], task_id=task.id)


async def test_invalid_resolution_keeps_escalation_open(handler_env: HandlerFixture) -> None:
    _task, escalation_id = await _escalated(
        handler_env, reason=EscalationReason.DECISION_REQUIRED, step=Step.IMPLEMENT
    )

    with pytest.raises(CommandRejectedError):
        await handler_env.registry.handle(
            Resolve(escalation_id=escalation_id, resolution=AcceptResolution())
        )

    escalation = handler_env.escalations.get(escalation_id)
    assert escalation is not None
    assert escalation.status is EscalationStatus.OPEN
