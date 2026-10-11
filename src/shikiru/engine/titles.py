"""`TaskTitles`: pane and window titles, and window state decorations (03 §4.2; 06 §5.3).

Refreshed at every task state boundary (dispatch, step, iteration, status, hold, rung and usage
change). The window's `@shk_state_color`/`@shk_state_glyph` mirror the task's own state — its
acting role and step, or a hold/escalation override — distinct from `engine.decorations`, which
mirrors a *session's* live activity on its own pane.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.agents.contracts import COST_BASIS_API_EQUIVALENT, COST_BASIS_BILLED, Usage
from shikiru.domain.ladder import pick_rung
from shikiru.domain.runs import NO_USAGE, RunStatus, context_level, context_title, cost_title
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
from shikiru.ui.tokens import (
    ALERT_COLOR,
    AMBER,
    CONTEXT_LEVEL_COLORS,
    ENGINE_COLOR,
    HOLD_COLOR,
    OPERATOR_COLOR,
    PRIMARY_TEXT_COLOR,
    ROLE_COLORS,
    SAGE,
    SECONDARY_TEXT_COLOR,
)

if TYPE_CHECKING:
    from shikiru.config.settings import Settings
    from shikiru.domain.runs import Run
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


def _pane_state(task: Task, role: Role, *, taken_over: bool) -> tuple[str, str]:
    if taken_over:
        return OPERATOR_COLOR, GLYPH_OPERATOR
    if Hold.PAUSED in task.holds:
        return HOLD_COLOR, GLYPH_PAUSED
    return ROLE_COLORS[role], ROLE_GLYPHS[role]


def _flag_parts(task: Task, *, taken_over: bool) -> list[tuple[str, str, str]]:
    flags: list[tuple[str, str, str]] = []
    if Hold.PAUSED in task.holds:
        flags.append((GLYPH_PAUSED, "paused", HOLD_COLOR))
    if taken_over or Hold.INTERVENTION in task.holds:
        flags.append((GLYPH_OPERATOR, "operator", OPERATOR_COLOR))
    if task.status is Status.ESCALATED:
        flags.append((GLYPH_ESCALATED, "escalated", ALERT_COLOR))
    return flags


def _flags(task: Task, *, taken_over: bool = False, include_glyphs: bool = True) -> str:
    flags = _flag_parts(task, taken_over=taken_over)
    if include_glyphs:
        return " ".join(f"{glyph} {word}" for glyph, word, _ in flags)
    return " ".join(word for _, word, _ in flags)


def _flag_segments(task: Task, *, taken_over: bool) -> tuple[tuple[str, str], ...]:
    parts = _flag_parts(task, taken_over=taken_over)
    segments: list[tuple[str, str]] = []
    for index, (_, word, color) in enumerate(parts):
        if index:
            segments.append((" ", PRIMARY_TEXT_COLOR))
        segments.append((word, color))
    return tuple(segments)


class TaskTitles:
    """Sets `task`'s window/pane titles and window state from `dashboard.titles` (06 §5.3).

    A role's pane taken over by the Operator (03 §9) is titled and coloured as the Operator's;
    otherwise it shows the pause hold or the role's own colour and glyph.
    """

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
        run = self._active_run(task.id, role)
        taken_over = run is not None and run.status is RunStatus.TAKEN_OVER
        usage = run.usage if run is not None else NO_USAGE
        usage_reported = run.usage_reported if run is not None else None
        template = getattr(self._settings.dashboard.titles, role.value)
        fields = {
            "task_id": task.id,
            "title": task.title,
            "project": self._settings.project.name,
            "role": role.value,
            "role_label": role.value.capitalize(),
            "glyph": GLYPH_OPERATOR if taken_over else ROLE_GLYPHS[role],
            "agent": rung.agent,
            "model": rung.model,
            "effort": rung.effort if rung.effort is not None else "",
            "iteration": task.iteration,
            "step": task.step.value if task.step is not None else "",
            "status": task.status.value,
            "context": context_title(usage),
            "cost": cost_title(usage),
            "flags": _flags(task),
            "flags_text": _flags(task, taken_over=taken_over, include_glyphs=False),
        }
        await self._decorator.set_pane_title(pane_id, template, fields)
        cost_color = SECONDARY_TEXT_COLOR
        if usage.cost_usd is not None and usage_reported is not False:
            if usage.cost_basis == COST_BASIS_BILLED:
                cost_color = SAGE
            elif usage.cost_basis == COST_BASIS_API_EQUIVALENT:
                cost_color = AMBER
        await self._decorator.set_pane_title_styling(
            pane_id,
            template,
            fields,
            field_colors={
                "role": ROLE_COLORS[role],
                "role_label": ROLE_COLORS[role],
                "agent": PRIMARY_TEXT_COLOR,
                "model": PRIMARY_TEXT_COLOR,
                "effort": SECONDARY_TEXT_COLOR,
                "iteration": SECONDARY_TEXT_COLOR,
                "context": self._context_color(usage, usage_reported),
                "cost": cost_color,
            },
            field_segments={"flags_text": _flag_segments(task, taken_over=taken_over)},
        )
        color, glyph = _pane_state(task, role, taken_over=taken_over)
        await self._decorator.set_pane_state(pane_id, color=color, glyph=glyph)

    def _context_color(self, usage: Usage, usage_reported: bool | None) -> str:
        if usage.context_used_pct is None or usage_reported is False:
            return SECONDARY_TEXT_COLOR
        titles = self._settings.dashboard.titles
        level = context_level(
            usage.context_used_pct,
            warn_pct=titles.context_warn_pct,
            alert_pct=titles.context_alert_pct,
        )
        return CONTEXT_LEVEL_COLORS[level]

    def _active_run(self, task_id: str, role: Role) -> Run | None:
        live = self._runs.get_live(task_id, role)
        return live if live is not None else self._runs.get_taken_over(task_id, role)
