"""`TaskTitles`: pane and window titles, and window state decorations (03 §4.2; 06 §5.3).

Refreshed by `TaskFlow` at every step boundary (step, iteration, status or hold change). The
window's `@shk_state_color`/`@shk_state_glyph` mirror the task's own state — its acting role and
step, or a hold/escalation override — distinct from `engine.decorations`, which mirrors a
*session's* live activity on its own pane.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.domain.ladder import pick_rung
from shikiru.domain.steps import ACTING_ROLE
from shikiru.domain.tasks import Hold, Role, Status, Step
from shikiru.ui.glyphs import (
    GLYPH_CONSULT,
    GLYPH_ENGINE,
    GLYPH_ESCALATED,
    GLYPH_OPERATOR,
    GLYPH_PAUSED,
    ROLE_GLYPHS,
    STAGE_IMPLEMENT,
    STAGE_MERGE,
    STAGE_PLAN,
    STAGE_TRIAGE,
    STAGE_VERIFY,
)
from shikiru.ui.tokens import ALERT_COLOR, ENGINE_COLOR, HOLD_COLOR, OPERATOR_COLOR, ROLE_COLORS

if TYPE_CHECKING:
    from shikiru.agents.contracts import Usage
    from shikiru.config.settings import Settings
    from shikiru.domain.tasks import Task
    from shikiru.store.runs import RunStore
    from shikiru.tmux.decorator import PaneDecorator

_STEP_GLYPHS: dict[Step, str] = {
    Step.TRIAGE: STAGE_TRIAGE,
    Step.PLAN: STAGE_PLAN,
    Step.IMPLEMENT: STAGE_IMPLEMENT,
    Step.CONSULT: GLYPH_CONSULT,
    Step.VERIFY: STAGE_VERIFY,
    Step.INTEGRATE: STAGE_MERGE,
}

_TITLED_ROLES = (Role.LEADER, Role.IMPLEMENTER)


def _window_state(task: Task) -> tuple[str, str]:
    if Hold.PAUSED in task.holds:
        return HOLD_COLOR, GLYPH_PAUSED
    if Hold.INTERVENTION in task.holds:
        return OPERATOR_COLOR, GLYPH_OPERATOR
    if task.status is Status.ESCALATED:
        return ALERT_COLOR, GLYPH_ESCALATED
    if task.step is None:
        return ENGINE_COLOR, GLYPH_ENGINE
    return ROLE_COLORS[ACTING_ROLE[task.step]], _STEP_GLYPHS[task.step]


def _flags(task: Task) -> str:
    tokens: list[str] = []
    if Hold.PAUSED in task.holds:
        tokens.append(f"{GLYPH_PAUSED} paused")
    if Hold.INTERVENTION in task.holds:
        tokens.append(f"{GLYPH_OPERATOR} operator")
    if task.status is Status.ESCALATED:
        tokens.append(f"{GLYPH_ESCALATED} escalated")
    return " ".join(tokens)


class TaskTitles:
    """Sets `task`'s window/pane titles and window state from `dashboard.titles` (06 §5.3)."""

    def __init__(self, decorator: PaneDecorator, runs: RunStore, settings: Settings) -> None:
        """Decorate through `decorator`, reading live usage from `runs`, titles from `settings`."""
        self._decorator = decorator
        self._runs = runs
        self._settings = settings

    async def refresh(self, task: Task) -> None:
        """Set `task`'s window title/state and its leader/implementer pane titles, if assigned."""
        if task.window_id is not None:
            await self._refresh_window(task, task.window_id)
        for role in _TITLED_ROLES:
            pane_id = task.panes.get(role)
            if pane_id is not None:
                await self._refresh_pane(task, role, pane_id)

    async def _refresh_window(self, task: Task, window_target: str) -> None:
        color, glyph = _window_state(task)
        await self._decorator.set_window_state(window_target, color=color, glyph=glyph)
        fields = {"task_id": task.id, "title": task.title}
        await self._decorator.set_window_title(
            window_target, self._settings.dashboard.titles.window, fields
        )

    async def _refresh_pane(self, task: Task, role: Role, pane_id: str) -> None:
        rung = pick_rung(task, role, self._settings)
        usage = self._latest_usage(task.id, role)
        template = getattr(self._settings.dashboard.titles, role.value)
        fields = {
            "task_id": task.id,
            "title": task.title,
            "project": self._settings.project.name,
            "role": role.value,
            "glyph": ROLE_GLYPHS[role],
            "agent": rung.agent,
            "model": rung.model,
            "effort": rung.effort if rung.effort is not None else "",
            "iteration": task.iteration,
            "step": task.step.value if task.step is not None else "",
            "status": task.status.value,
            "context": _context_pct(usage),
            "cost": _cost_usd(usage),
            "flags": _flags(task),
        }
        await self._decorator.set_pane_title(pane_id, template, fields)

    def _latest_usage(self, task_id: str, role: Role) -> Usage | None:
        run = self._runs.get_live(task_id, role)
        return run.usage if run is not None else None


def _context_pct(usage: Usage | None) -> int:
    if usage is None or usage.context_used_pct is None:
        return 0
    return round(usage.context_used_pct)


def _cost_usd(usage: Usage | None) -> float:
    if usage is None or usage.cost_usd is None:
        return 0.0
    return usage.cost_usd
