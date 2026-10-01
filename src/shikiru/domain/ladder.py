"""Rung selection, move-up rules and counter resets (docs/design/03-workflow.md §1.1, §3, §7)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from shikiru.domain.tasks import DEFAULT_COMPLEXITY, Counters, Role, Task

if TYPE_CHECKING:
    from shikiru.agents.contracts import Rung
    from shikiru.config.settings import Settings

REJECTIONS_FIELD = "rejections_on_rung"


def _ladder_for(role: Role, task: Task, settings: Settings) -> list[Rung]:
    if role is Role.LEADER:
        return settings.roles.leader.ladder
    if role is Role.IMPLEMENTER:
        return settings.roles.implementer.ladder_for(task.complexity or DEFAULT_COMPLEXITY)
    return settings.roles.helper.ladder


def pick_rung(task: Task, role: Role, settings: Settings) -> Rung:
    """Resolve `role`'s rung: the operator override, else the ladder at `rung_index[role]`."""
    override = task.overrides.get(role)
    if override is not None:
        return override
    ladder = _ladder_for(role, task, settings)
    return ladder[task.rung_index.get(role, 0)]


def reset_for_rung_change(counters: Counters) -> Counters:
    """`rejections_on_rung` resets whenever the Implementer's rung changes (03 §1.1)."""
    return counters.model_copy(update={REJECTIONS_FIELD: 0})


def reset_for_new_iteration(counters: Counters) -> Counters:
    """`consults` resets at the start of every implement/verify iteration (03 §1.1)."""
    return counters.model_copy(update={"consults": 0})


def reset_for_integration_attempt(counters: Counters) -> Counters:
    """`conflict_rounds` resets at the start of every integration attempt (03 §1.1)."""
    return counters.model_copy(update={"conflict_rounds": 0})


@dataclass(frozen=True)
class RungMove:
    """The new `rung_index`, the override to clear and `counters` after moving a role up."""

    rung_index: dict[Role, int]
    cleared_overrides: dict[Role, Rung]
    counters: Counters


def move_up(
    task: Task, role: Role, settings: Settings, *, different_agent: bool = False
) -> RungMove | None:
    """Advance `role` one rung up its ladder from the rung currently resolved by `pick_rung`.

    Comparing against the resolved rung, not `ladder[rung_index]`, matters because that rung may
    be an operator override; `different_agent=True` then skips ladder rungs on that override's
    agent too. Moving up clears `role`'s override as observed in `task`: an automatic move replaces
    a manual pin, since keeping it would make the move a no-op and could loop recovery on a
    blocked agent. The store clears it only while it still equals that observed rung, so a pin the
    operator set since survives.
    For the Implementer, `rejections_on_rung` also resets, since it counts rework at one rung.

    Returns `None` once no further rung satisfies `different_agent` above the current one; the
    caller then keeps the task on its current rung (03 §3, §7).
    """
    ladder = _ladder_for(role, task, settings)
    current_rung = pick_rung(task, role, settings)
    current_index = task.rung_index.get(role, 0)
    for index in range(current_index + 1, len(ladder)):
        candidate = ladder[index]
        if different_agent and candidate.agent == current_rung.agent:
            continue
        rung_index = dict(task.rung_index)
        rung_index[role] = index
        cleared = {role: task.overrides[role]} if role in task.overrides else {}
        counters = (
            reset_for_rung_change(task.counters) if role is Role.IMPLEMENTER else task.counters
        )
        return RungMove(rung_index=rung_index, cleared_overrides=cleared, counters=counters)
    return None
