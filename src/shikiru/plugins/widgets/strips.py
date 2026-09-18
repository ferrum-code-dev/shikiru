"""The `strips` widget: active tasks as comic-strip panels (docs/design/06-operator.md §6.2)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.widgets import Static

from shikiru.config.loader import load_settings
from shikiru.domain.tasks import Hold, Mode, Role, Status, Step, Task
from shikiru.ui.glyphs import ROLE_GLYPHS, SPINNER_FRAMES
from shikiru.ui.strip import (
    STRIP_WIDE_MIN_COLUMNS,
    PanelState,
    PanelView,
    StampKind,
    StripStamp,
    TaskStrip,
    TaskStripData,
    panel_specs_for_mode,
)

if TYPE_CHECKING:
    from textual.widget import Widget

    from shikiru.agents.contracts import Rung
    from shikiru.config.settings import Settings
    from shikiru.dashboard.contract import WidgetContext
    from shikiru.store.reader import StateReader

_ACTIVE_STATUSES = (Status.RUNNING, Status.INTEGRATING, Status.ESCALATED)
_PANEL_INDEX: dict[Step, int] = {
    Step.TRIAGE: 0,
    Step.PLAN: 1,
    Step.IMPLEMENT: 1,
    Step.CONSULT: 1,
    Step.VERIFY: 2,
    Step.INTEGRATE: 3,
}
_STEP_ROLE: dict[Step, Role] = {
    Step.TRIAGE: Role.LEADER,
    Step.PLAN: Role.LEADER,
    Step.IMPLEMENT: Role.IMPLEMENTER,
    Step.CONSULT: Role.LEADER,
    Step.VERIFY: Role.LEADER,
    Step.INTEGRATE: Role.LEADER,
}


def _rung_for(task: Task, role: Role, settings: Settings) -> Rung | None:
    override = task.overrides.get(role)
    if override is not None:
        return override
    if role is Role.LEADER:
        ladder = settings.roles.leader.ladder
    elif role is Role.IMPLEMENTER:
        ladder = settings.roles.implementer.ladder_for(task.complexity)
    else:
        ladder = settings.roles.helper.ladder
    if not ladder:
        return None
    index = min(task.rung_index.get(role, 0), len(ladder) - 1)
    return ladder[index]


def _stamp_for(task: Task) -> StripStamp | None:
    if Hold.PAUSED in task.holds:
        return StripStamp(StampKind.PAUSED)
    if Hold.INTERVENTION in task.holds:
        return StripStamp(StampKind.OPERATOR)
    return None


def _panels_for(task: Task, settings: Settings) -> tuple[PanelView, ...]:
    mode = task.mode or Mode.IMPLEMENT
    specs = panel_specs_for_mode(mode)
    current_index = _PANEL_INDEX.get(task.step, 0) if task.step is not None else 0
    panels: list[PanelView] = []
    for index, (label, glyph) in enumerate(specs):
        if index < current_index:
            state = PanelState.DONE
        elif index == current_index:
            state = PanelState.ESCALATED if task.status is Status.ESCALATED else PanelState.ACTIVE
        else:
            state = PanelState.PENDING
        detail = role_line = spinner_frame = ""
        if state in (PanelState.ACTIVE, PanelState.ESCALATED) and task.step is not None:
            role = _STEP_ROLE.get(task.step, Role.LEADER)
            rung = _rung_for(task, role, settings)
            detail = f"iter {task.iteration}" if task.iteration else ""
            if rung is not None:
                role_line = f"{ROLE_GLYPHS[role]} {rung.agent}/{rung.model}"
            if state is PanelState.ACTIVE:
                spinner_frame = SPINNER_FRAMES[0]
        panels.append(
            PanelView(
                label=label,
                stage_glyph=glyph,
                state=state,
                detail=detail,
                role_line=role_line,
                spinner_frame=spinner_frame,
            )
        )
    return tuple(panels)


def _strip_data(task: Task, settings: Settings) -> TaskStripData:
    return TaskStripData(
        task_id=task.id,
        title=task.title,
        panels=_panels_for(task, settings),
        stamp=_stamp_for(task),
    )


def _split_strip_data(parent: Task, children: list[str]) -> TaskStripData:
    return TaskStripData(task_id=parent.id, title=parent.title, split_children=tuple(children))


def _rows(state: StateReader, focus_task: str | None, settings: Settings) -> list[str]:
    active = state.list_tasks(statuses=_ACTIVE_STATUSES)
    active.sort(key=lambda task: task.id != focus_task)
    rendered: list[str] = []
    seen_parents: set[str] = set()
    for task in active:
        parent = state.get_task(task.parent_id) if task.parent_id is not None else None
        if parent is not None and parent.status_reason == "split" and parent.id not in seen_parents:
            seen_parents.add(parent.id)
            children = [
                sibling.id for sibling in state.list_tasks() if sibling.parent_id == parent.id
            ]
            split_data = _split_strip_data(parent, children)
            rendered.append(TaskStrip(split_data).render(STRIP_WIDE_MIN_COLUMNS).plain)
        data = _strip_data(task, settings)
        rendered.append(TaskStrip(data).render(STRIP_WIDE_MIN_COLUMNS).plain)
    return rendered


class StripsPanel(Static):
    """Renders every active task's strip, in dispatch order, focused task last expanded."""

    def __init__(self, context: WidgetContext) -> None:
        """Load `context.project_dir`'s settings once, for ladder and role resolution."""
        super().__init__()
        self._widget_context = context
        self._settings = load_settings(context.project_dir)

    async def on_mount(self) -> None:
        """Render the current strips, then refresh on every `StateReader.watch` change."""
        await self._refresh()
        self.run_worker(self._widget_context.state.watch(self._refresh), exclusive=True)

    async def _refresh(self) -> None:
        lines = _rows(self._widget_context.state, self._widget_context.focus_task, self._settings)
        self.update("\n\n".join(lines) if lines else "no active tasks")


class StripsWidget:
    """The `strips` dashboard widget plugin (06 §5.4, §6.2)."""

    name = "strips"

    def create(self, context: WidgetContext) -> Widget:
        """Build the `StripsPanel` for `context`."""
        return StripsPanel(context)
