"""Build task-strip view data from domain state for operator interfaces (06 §6.2)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.domain.ladder import pick_rung
from shikiru.domain.steps import ACTING_ROLE, Step
from shikiru.domain.tasks import Hold, Status, Task
from shikiru.ui.glyphs import (
    GLYPH_DONE,
    GLYPH_ENGINE,
    GLYPH_ESCALATED,
    GLYPH_PENDING,
    ROLE_GLYPHS,
    SPINNER_FRAMES,
    STAGE_TRIAGE,
)
from shikiru.ui.strip import (
    PanelState,
    PanelView,
    StampKind,
    StripStamp,
    TaskStripData,
    panel_specs_for_mode,
)

if TYPE_CHECKING:
    from shikiru.config.settings import Settings

_PANEL_INDEX: dict[Step, int] = {
    Step.TRIAGE: 0,
    Step.PLAN: 1,
    Step.IMPLEMENT: 1,
    Step.CONSULT: 1,
    Step.VERIFY: 2,
    Step.INTEGRATE: 3,
}


def task_strip_data(task: Task, settings: Settings) -> TaskStripData:
    """Build the render data for one task using the configured role ladder."""
    if task.step is None:
        if task.status is Status.RUNNING:
            return _starting_strip_data(task)
        if task.status not in (Status.QUEUED, Status.DONE, Status.CANCELLED, Status.ESCALATED):
            message = f"task {task.id} is active but has no step recorded"
            raise ValueError(message)
        return _unstarted_strip_data(task)
    if task.mode is None:
        if task.step is not Step.TRIAGE:
            message = f"task {task.id} has no mode yet but is at step {task.step!r}, not triage"
            raise ValueError(message)
        specs = (("Triage", STAGE_TRIAGE),)
    else:
        specs = panel_specs_for_mode(task.mode)
    active_state = _active_panel_state(task)
    current_index = _PANEL_INDEX[task.step]
    panels = tuple(
        _panel_view(
            label,
            glyph,
            PanelState.DONE
            if index < current_index
            else (active_state if index == current_index else PanelState.PENDING),
            step=task.step,
            task=task,
            settings=settings,
        )
        for index, (label, glyph) in enumerate(specs)
    )
    return TaskStripData(
        task_id=task.id,
        title=task.title,
        panels=panels,
        stamp=_stamp_for(task),
    )


def _unstarted_strip_data(task: Task) -> TaskStripData:
    if task.status is Status.QUEUED:
        state, glyph = PanelState.PENDING, GLYPH_PENDING
    elif task.status is Status.DONE:
        state, glyph = PanelState.DONE, GLYPH_DONE
    elif task.status in (Status.CANCELLED, Status.ESCALATED):
        state, glyph = PanelState.ESCALATED, GLYPH_ESCALATED
    else:
        message = f"task {task.id} has no step for status {task.status.value!r}"
        raise ValueError(message)
    panel = PanelView(label=task.status.value.title(), stage_glyph=glyph, state=state)
    return TaskStripData(
        task_id=task.id,
        title=task.title,
        panels=(panel,),
        stamp=_stamp_for(task),
    )


def _starting_strip_data(task: Task) -> TaskStripData:
    panel = PanelView(
        label="Starting",
        stage_glyph=GLYPH_ENGINE,
        state=PanelState.ACTIVE,
        detail="setting up",
        spinner_frame=SPINNER_FRAMES[0],
    )
    return TaskStripData(
        task_id=task.id,
        title=task.title,
        panels=(panel,),
        stamp=_stamp_for(task),
    )


def _active_panel_state(task: Task) -> PanelState:
    if task.status in (Status.ESCALATED, Status.CANCELLED):
        return PanelState.ESCALATED
    if task.status is Status.DONE:
        return PanelState.DONE
    return PanelState.ACTIVE


def _panel_view(
    label: str, glyph: str, state: PanelState, *, step: Step, task: Task, settings: Settings
) -> PanelView:
    if state not in (PanelState.ACTIVE, PanelState.ESCALATED):
        return PanelView(label=label, stage_glyph=glyph, state=state)
    role = ACTING_ROLE[step]
    rung = pick_rung(task, role, settings)
    detail = f"iter {task.iteration}" if task.iteration else ""
    role_line = f"{ROLE_GLYPHS[role]} {rung.agent}/{rung.model}"
    spinner_frame = SPINNER_FRAMES[0] if state is PanelState.ACTIVE else ""
    return PanelView(
        label=label,
        stage_glyph=glyph,
        state=state,
        detail=detail,
        role_line=role_line,
        spinner_frame=spinner_frame,
        role=role if state is PanelState.ACTIVE else None,
    )


def _stamp_for(task: Task) -> StripStamp | None:
    if Hold.PAUSED in task.holds:
        return StripStamp(StampKind.PAUSED)
    if Hold.INTERVENTION in task.holds:
        return StripStamp(StampKind.OPERATOR)
    return None
