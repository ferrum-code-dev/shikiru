"""The seeded schedule of one arena: what is submitted, what the operator does, what breaks."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from shikiru.domain.tasks import Mode
from tests.soak.model import (
    FakeStep,
    FaultKind,
    IntegrationMode,
    OperatorKind,
    ScheduledFault,
    ScheduledOperation,
    Scripts,
    TaskKind,
    TaskPlan,
)

TASK_PLACEHOLDER = "{task}"
RESETS_AT_PLACEHOLDER = "{resets_at}"
PROTOCOL_ROUNDS = 3
WARMUP_SECONDS = 8.0
SUBMISSION_GAP_SECONDS = (2.0, 6.0)
OPERATION_GAP_SECONDS = (1.5, 4.0)
FAULT_GAP_SECONDS = (8.0, 18.0)
FREEZE_SECONDS = (2.0, 6.0)
DB_LOCK_SECONDS = (2.0, 8.0)
HOOK_STUCK_SECONDS = (5.0, 40.0)
REMOTE_DOWN_SECONDS = (3.0, 10.0)
INSTANT_SECONDS = 0.0
MAX_ROUNDS = 5
DECISION_ROUNDS = 4
MAX_DEPENDENCIES = 2
AFTER_PROBABILITY = 0.25
TRIAGE_PROBABILITY = 0.3
PRIORITY_RANGE = (0, 5)
NEW_TASK_COUNT = (2, 3)
REJECTIONS = (1, 2)

_ACCEPT: FakeStep = {"kind": "accept"}
_CHANGES_REQUESTED: FakeStep = {"kind": "changes_requested"}
_IMPLEMENTER_STEPS: dict[TaskKind, list[FakeStep]] = {
    TaskKind.AUTH_FAILURE: [{"kind": "fail", "failure_kind": "auth"}],
    TaskKind.OTHER_FAILURE: [{"kind": "fail", "failure_kind": "other"}],
    TaskKind.DECISION: [{"kind": "needs_decision"}] * DECISION_ROUNDS,
    TaskKind.UNAVAILABLE: [{"kind": "fail", "failure_kind": "unavailable"}],
    TaskKind.QUOTA: [{"kind": "fail", "failure_kind": "quota", "resets_at": RESETS_AT_PLACEHOLDER}],
    TaskKind.PROTOCOL: [{"kind": "no_report"}] * PROTOCOL_ROUNDS,
    TaskKind.STALL: [{"kind": "stall"}],
}
_TASK_WEIGHTS = {
    TaskKind.SIMPLE: 40,
    TaskKind.REJECTED: 14,
    TaskKind.ROUNDS_EXHAUSTED: 4,
    TaskKind.AUTH_FAILURE: 6,
    TaskKind.OTHER_FAILURE: 5,
    TaskKind.DECISION: 5,
    TaskKind.PLAN: 8,
    TaskKind.SUPERSEDED: 5,
    TaskKind.UNCOMMITTED: 6,
    TaskKind.UNAVAILABLE: 3,
    TaskKind.QUOTA: 2,
    TaskKind.PROTOCOL: 3,
    TaskKind.STALL: 2,
}
_OPERATION_WEIGHTS = {
    OperatorKind.SAY: 24,
    OperatorKind.PRIORITIZE: 14,
    OperatorKind.PAUSE: 10,
    OperatorKind.RESUME: 6,
    OperatorKind.SET_RUNG: 10,
    OperatorKind.TAKE_OVER: 9,
    OperatorKind.HAND_BACK: 6,
    OperatorKind.CANCEL: 3,
    OperatorKind.RETRY: 7,
}
_FAULT_WEIGHTS = {
    FaultKind.ENGINE_KILL: 3,
    FaultKind.ENGINE_KILL_LOCKED: 2,
    FaultKind.ENGINE_KILL_INTEGRATING: 4,
    FaultKind.ENGINE_KILL_ESCALATING: 3,
    FaultKind.ENGINE_TERM: 2,
    FaultKind.ENGINE_INT: 1,
    FaultKind.ENGINE_STOP_START: 1,
    FaultKind.ENGINE_FREEZE: 2,
    FaultKind.TMUX_LOSS: 2,
    FaultKind.TMUX_LOSS_TAKEOVER: 2,
    FaultKind.REMOTE_DOWN: 3,
    FaultKind.TURN_KILL: 5,
    FaultKind.DB_LOCK: 3,
    FaultKind.GH_FAIL: 3,
    FaultKind.GIT_HOOK_FAIL: 3,
    FaultKind.HOOK_STUCK: 1,
    FaultKind.HOOK_FAIL: 1,
}
PR_ONLY_FAULTS = (FaultKind.GH_FAIL, FaultKind.REMOTE_DOWN)
_FAULT_DURATIONS = {
    FaultKind.REMOTE_DOWN: REMOTE_DOWN_SECONDS,
    FaultKind.ENGINE_FREEZE: FREEZE_SECONDS,
    FaultKind.DB_LOCK: DB_LOCK_SECONDS,
    FaultKind.ENGINE_KILL_LOCKED: DB_LOCK_SECONDS,
    FaultKind.HOOK_STUCK: HOOK_STUCK_SECONDS,
    FaultKind.HOOK_FAIL: HOOK_STUCK_SECONDS,
}


@dataclass(frozen=True, slots=True)
class Submission:
    offset_seconds: float
    plan: TaskPlan
    after_pick: float


@dataclass(frozen=True, slots=True)
class Schedule:
    submissions: tuple[Submission, ...]
    operations: tuple[ScheduledOperation, ...]
    faults: tuple[ScheduledFault, ...]


def _weighted[T](rng: random.Random, weights: dict[T, int]) -> T:
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def _spawned(rng: random.Random, tag: str) -> list[dict[str, Any]]:
    return [
        {"title": f"{tag} part {index}", "request": f"Implement {tag} part {index}"}
        for index in range(rng.randint(*NEW_TASK_COUNT))
    ]


def _scripts(kind: TaskKind, rng: random.Random, tag: str, *, triaged: bool) -> Scripts:
    triage = [_ACCEPT] if triaged else []
    if kind in _IMPLEMENTER_STEPS:
        return {"implementer": _IMPLEMENTER_STEPS[kind]}
    match kind:
        case TaskKind.REJECTED:
            rejections = [_CHANGES_REQUESTED] * rng.randint(*REJECTIONS)
            return {"leader": [*triage, *rejections, _ACCEPT]}
        case TaskKind.ROUNDS_EXHAUSTED:
            return {"leader": [*triage, *[_CHANGES_REQUESTED] * MAX_ROUNDS]}
        case TaskKind.PLAN:
            return {"leader": [{"kind": "accept", "new_tasks": _spawned(rng, tag)}]}
        case TaskKind.SUPERSEDED:
            return {"leader": [*triage, {"kind": "supersede", "new_tasks": _spawned(rng, tag)}]}
        case TaskKind.UNCOMMITTED:
            scratch = f"scratch-{TASK_PLACEHOLDER}.txt"
            return {
                "leader": [*triage, _ACCEPT, _ACCEPT],
                "implementer": [{"kind": "accept", "uncommitted": scratch}],
            }
        case _:
            return {"leader": [*triage, _ACCEPT]}


def _plan(rng: random.Random, index: int) -> TaskPlan:
    kind = _weighted(rng, _TASK_WEIGHTS)
    triaged = kind is not TaskKind.PLAN and rng.random() < TRIAGE_PROBABILITY
    tag = f"soak task {index} ({kind.value})"
    mode = None if triaged else (Mode.PLAN if kind is TaskKind.PLAN else Mode.IMPLEMENT)
    return TaskPlan(
        kind=kind,
        request=f"Implement {tag}",
        mode=mode,
        priority=rng.randint(*PRIORITY_RANGE),
        after=(),
        scripts=_scripts(kind, rng, tag, triaged=triaged),
    )


def _offsets(rng: random.Random, gap: tuple[float, float], start: float, end: float) -> list[float]:
    offsets: list[float] = []
    at = start + rng.uniform(*gap)
    while at < end:
        offsets.append(at)
        at += rng.uniform(*gap)
    return offsets


def _fault(rng: random.Random, offset: float, mode: IntegrationMode) -> ScheduledFault:
    weights = {
        kind: weight
        for kind, weight in _FAULT_WEIGHTS.items()
        if mode is IntegrationMode.PR or kind not in PR_ONLY_FAULTS
    }
    kind = _weighted(rng, weights)
    low_high = _FAULT_DURATIONS.get(kind)
    duration = rng.uniform(*low_high) if low_high else INSTANT_SECONDS
    return ScheduledFault(offset, kind, duration)


def build_schedule(
    seed: str, mode: IntegrationMode, *, intake_seconds: float, fault_free: bool = False
) -> Schedule:
    """Draw one arena's submissions, operator operations and faults, all from `seed`."""
    rng = random.Random(seed)
    submissions = tuple(
        Submission(offset, _plan(rng, index), rng.random())
        for index, offset in enumerate(
            _offsets(rng, SUBMISSION_GAP_SECONDS, 0.0, intake_seconds), start=1
        )
    )
    operations = tuple(
        ScheduledOperation(offset, _weighted(rng, _OPERATION_WEIGHTS), rng.random())
        for offset in _offsets(rng, OPERATION_GAP_SECONDS, WARMUP_SECONDS, intake_seconds)
    )
    faults = (
        ()
        if fault_free
        else tuple(
            _fault(rng, offset, mode)
            for offset in _offsets(rng, FAULT_GAP_SECONDS, WARMUP_SECONDS, intake_seconds)
        )
    )
    return Schedule(submissions, operations, faults)
