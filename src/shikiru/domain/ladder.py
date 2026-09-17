"""Rung selection, move-up rules and counter resets (docs/design/03-workflow.md §1.1, §3, §7)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from shikiru.domain.tasks import Counters, EscalationReason, Role, Task

if TYPE_CHECKING:
    from shikiru.agents.contracts import Rung
    from shikiru.config.settings import Settings


def _ladder_for(role: Role, task: Task, settings: Settings) -> list[Rung]:
    if role is Role.LEADER:
        return settings.roles.leader.ladder
    if role is Role.IMPLEMENTER:
        return settings.roles.implementer.ladder_for(task.complexity)
    return settings.roles.helper.ladder


def pick_rung(task: Task, role: Role, settings: Settings) -> Rung:
    """Resolve `role`'s rung: the operator override, else the ladder at `rung_index[role]`."""
    override = task.overrides.get(role)
    if override is not None:
        return override
    ladder = _ladder_for(role, task, settings)
    return ladder[task.rung_index.get(role, 0)]


@dataclass(frozen=True)
class RungAdvance:
    """The next rung up a role's ladder, and its index for persisting `rung_index`."""

    rung: Rung
    index: int


def next_rung(
    task: Task, role: Role, settings: Settings, *, different_agent: bool = False
) -> RungAdvance | None:
    """Return the next rung up `role`'s ladder above `rung_index[role]`, or `None` when exhausted.

    Move-up always progresses from `rung_index`, ignoring an operator override: an override is
    a manual pin, not a point on the automatic ladder. With `different_agent=True`, rungs on the
    current agent are skipped, as recovery needs for a quota exhaustion or a crashed agent.
    """
    ladder = _ladder_for(role, task, settings)
    current_index = task.rung_index.get(role, 0)
    current_agent = ladder[current_index].agent
    for index in range(current_index + 1, len(ladder)):
        candidate = ladder[index]
        if different_agent and candidate.agent == current_agent:
            continue
        return RungAdvance(rung=candidate, index=index)
    return None


def reset_for_rung_change(counters: Counters) -> Counters:
    """`rejections_on_rung` resets whenever a task's rung changes (03 §1.1)."""
    return counters.model_copy(update={"rejections_on_rung": 0})


def reset_for_new_iteration(counters: Counters) -> Counters:
    """`consults` resets at the start of every implement/verify iteration (03 §1.1)."""
    return counters.model_copy(update={"consults": 0})


def reset_for_integration_attempt(counters: Counters) -> Counters:
    """`conflict_rounds` resets at the start of every integration attempt (03 §1.1)."""
    return counters.model_copy(update={"conflict_rounds": 0})


_RESET_FIELD_BY_REASON: dict[EscalationReason, str] = {
    EscalationReason.DECISION_REQUIRED: "consults",
    EscalationReason.ITERATIONS_EXHAUSTED: "rejections_on_rung",
    EscalationReason.INTEGRATION_CONFLICT: "conflict_rounds",
}


def reset_for_resolved_escalation(counters: Counters, reason: EscalationReason) -> Counters:
    """Reset the counter behind `reason` once its escalation resolves (03 §1.1)."""
    field = _RESET_FIELD_BY_REASON.get(reason)
    if field is None:
        return counters
    return counters.model_copy(update={field: 0})
