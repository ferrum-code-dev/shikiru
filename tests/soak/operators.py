"""The operator: issues commands at random, undoes its own holds, and resolves escalations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from shikiru.agents.contracts import Rung
from shikiru.clock import SystemClock
from shikiru.domain.commands import (
    AcceptResolution,
    AnswerResolution,
    Cancel,
    CancelResolution,
    Command,
    HandBack,
    Pause,
    Prioritize,
    Resolution,
    Resolve,
    Resume,
    Retry,
    RetryResolution,
    Say,
    SetRung,
    Submit,
    TakeOver,
)
from shikiru.domain.tasks import Hold, Role, Status, Task
from tests.soak.model import OperatorKind, Tally
from tests.soak.scenarios import AFTER_PROBABILITY, Submission

if TYPE_CHECKING:
    import random

    from shikiru.domain.escalation import Escalation
    from tests.soak.arena import Arena
    from tests.soak.invariants import Ledger

CLOCK = SystemClock()
ROLES = (Role.LEADER, Role.IMPLEMENTER)
RUNGS = (Rung(agent="fake", model="fake-1"), Rung(agent="fake", model="fake-2"))
RELEASE_AFTER_SECONDS = (3.0, 8.0)
RESOLVE_AFTER_SECONDS = (1.5, 5.0)
PRIORITY_SPAN = 10
CANCEL_SHARE = 0.15
ANSWER_SHARE = 0.4
ACCEPT_SHARE = 0.4
ANSWER_TEXT = "soak: take the simplest approach that satisfies the request"
LIVE = (Status.QUEUED, Status.RUNNING, Status.INTEGRATING, Status.ESCALATED)


@dataclass(frozen=True, slots=True)
class _Release:
    due: datetime
    command: Command


class OperatorDesk:
    def __init__(self, arena: Arena, ledger: Ledger, tally: Tally, rng: random.Random) -> None:
        self._arena = arena
        self._ledger = ledger
        self._tally = tally
        self._rng = rng
        self._releases: list[_Release] = []
        self._resolving: dict[str, datetime] = {}
        self._counter = 0

    async def _issue(self, command: Command) -> int:
        command_id, queued = await self._arena.append(command)
        self._ledger.commands[command_id] = queued.kind.value
        self._tally.command(queued.kind.value)
        return command_id

    async def submit(self, submission: Submission) -> str:
        plan = submission.plan
        reader = self._arena.reader
        assert reader is not None
        earlier = [task.id for task in reader.list_tasks() if task.status is not Status.CANCELLED]
        pick = submission.after_pick / AFTER_PROBABILITY
        after = (earlier[int(pick * len(earlier))],) if earlier and pick < 1 else ()
        command = Submit(
            title=plan.request[:60],
            request=plan.request,
            mode=plan.mode,
            priority=plan.priority,
            depends_on=list(after),
        )
        _, queued = await self._arena.append(command)
        assert isinstance(queued, Submit)
        task_id = queued.task_id
        assert task_id is not None
        self._arena.write_script(task_id, plan.scripts)
        self._ledger.kinds[task_id] = plan.kind.value
        self._tally.submitted += 1
        return task_id

    async def operate(self, kind: OperatorKind, pick: float) -> str | None:
        reader = self._arena.reader
        assert reader is not None
        tasks = reader.list_tasks()
        task = _choose(tasks, kind, pick)
        if task is None:
            return None
        role = ROLES[int(pick * len(tasks) * len(ROLES)) % len(ROLES)]
        command = self._command(kind, task, role, pick)
        await self._issue(command)
        self._plan_release(command)
        return f"{kind.value} {task.id}"

    def _command(self, kind: OperatorKind, task: Task, role: Role, pick: float) -> Command:
        self._counter += 1
        match kind:
            case OperatorKind.SAY:
                return Say(task_id=task.id, role=role, text=f"soak message {self._counter}")
            case OperatorKind.PRIORITIZE:
                return Prioritize(task_id=task.id, priority=int(pick * PRIORITY_SPAN))
            case OperatorKind.PAUSE:
                return Pause(task_id=task.id, now=pick > 0.5)
            case OperatorKind.RESUME:
                return Resume(task_id=task.id)
            case OperatorKind.SET_RUNG:
                return SetRung(task_id=task.id, role=role, rung=RUNGS[int(pick * len(RUNGS))])
            case OperatorKind.TAKE_OVER:
                return TakeOver(task_id=task.id, role=role)
            case OperatorKind.HAND_BACK:
                return HandBack(task_id=task.id, role=role)
            case OperatorKind.CANCEL:
                return Cancel(task_id=task.id)
            case OperatorKind.RETRY:
                return Retry(task_id=task.id)

    def _plan_release(self, command: Command) -> None:
        after = timedelta(seconds=self._rng.uniform(*RELEASE_AFTER_SECONDS))
        if isinstance(command, Pause):
            self._releases.append(_Release(CLOCK.now() + after, Resume(task_id=command.task_id)))
        if isinstance(command, TakeOver):
            self._releases.append(
                _Release(CLOCK.now() + after, HandBack(task_id=command.task_id, role=command.role))
            )

    async def release_due(self, *, everything: bool) -> None:
        now = CLOCK.now()
        due = [item for item in self._releases if everything or item.due <= now]
        self._releases = [item for item in self._releases if item not in due]
        for item in due:
            await self._issue(item.command)

    async def release_holds(self) -> None:
        reader = self._arena.reader
        assert reader is not None
        for task in reader.list_tasks():
            if task.status in LIVE and Hold.PAUSED in task.holds:
                await self._issue(Resume(task_id=task.id))
            if task.status in LIVE and Hold.INTERVENTION in task.holds:
                for role in ROLES:
                    await self._issue(HandBack(task_id=task.id, role=role))

    async def resolve_escalations(self) -> None:
        reader = self._arena.reader
        assert reader is not None
        now = CLOCK.now()
        open_ones = reader.list_open_escalations()
        for escalation in open_ones:
            due = self._resolving.setdefault(
                escalation.id, now + timedelta(seconds=self._rng.uniform(*RESOLVE_AFTER_SECONDS))
            )
            if now >= due:
                self._resolving[escalation.id] = now + timedelta(seconds=sum(RESOLVE_AFTER_SECONDS))
                await self._issue(
                    Resolve(escalation_id=escalation.id, resolution=self._resolution(escalation))
                )

    def _resolution(self, escalation: Escalation) -> Resolution:
        roll = self._rng.random()
        if roll < CANCEL_SHARE:
            return CancelResolution()
        labels = {option.label.value for option in escalation.options}
        if "answer" in labels and roll < CANCEL_SHARE + ANSWER_SHARE:
            return AnswerResolution(text=ANSWER_TEXT)
        if "accept" in labels and roll < CANCEL_SHARE + ANSWER_SHARE + ACCEPT_SHARE:
            return AcceptResolution()
        if "retry" in labels:
            return RetryResolution()
        return AnswerResolution(text=ANSWER_TEXT)


def _choose(tasks: list[Task], kind: OperatorKind, pick: float) -> Task | None:
    live = [task for task in tasks if task.status in LIVE]
    pool = {
        OperatorKind.PAUSE: [task for task in live if Hold.PAUSED not in task.holds],
        OperatorKind.RESUME: [task for task in live if task.holds],
        OperatorKind.TAKE_OVER: [task for task in live if task.status is Status.RUNNING],
        OperatorKind.HAND_BACK: [task for task in live if Hold.INTERVENTION in task.holds],
        OperatorKind.RETRY: [
            task for task in live if task.status in (Status.RUNNING, Status.ESCALATED)
        ],
    }.get(kind, live)
    return pool[int(pick * len(pool))] if pool else None
