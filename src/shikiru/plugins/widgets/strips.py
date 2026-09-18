"""The `strips` widget: active tasks as comic-strip panels (docs/design/06-operator.md §6.2)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual.widgets import Static

from shikiru.domain.ladder import pick_rung
from shikiru.domain.steps import ACTING_ROLE
from shikiru.domain.tasks import Hold, Status, Step, Task
from shikiru.ui.glyphs import ROLE_GLYPHS, SPINNER_FRAMES, STAGE_TRIAGE
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
    from textual import events
    from textual.widget import Widget

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


def _active_panel_state(task: Task) -> PanelState:
    return PanelState.ESCALATED if task.status is Status.ESCALATED else PanelState.ACTIVE


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
    )


def _panels_for(task: Task, settings: Settings) -> tuple[PanelView, ...]:
    if task.step is None:
        message = f"task {task.id} is active but has no step recorded"
        raise ValueError(message)
    step: Step = task.step
    active_state = _active_panel_state(task)
    if task.mode is None:
        if step is not Step.TRIAGE:
            message = f"task {task.id} has no mode yet but is at step {step!r}, not triage"
            raise ValueError(message)
        panel = _panel_view(
            "Triage", STAGE_TRIAGE, active_state, step=step, task=task, settings=settings
        )
        return (panel,)
    specs = panel_specs_for_mode(task.mode)
    current_index = _PANEL_INDEX[step]
    panels: list[PanelView] = []
    for index, (label, glyph) in enumerate(specs):
        if index < current_index:
            state = PanelState.DONE
        elif index == current_index:
            state = active_state
        else:
            state = PanelState.PENDING
        view = _panel_view(label, glyph, state, step=step, task=task, settings=settings)
        panels.append(view)
    return tuple(panels)


def _stamp_for(task: Task) -> StripStamp | None:
    if Hold.PAUSED in task.holds:
        return StripStamp(StampKind.PAUSED)
    if Hold.INTERVENTION in task.holds:
        return StripStamp(StampKind.OPERATOR)
    return None


def _strip_data(task: Task, settings: Settings) -> TaskStripData:
    return TaskStripData(
        task_id=task.id,
        title=task.title,
        panels=_panels_for(task, settings),
        stamp=_stamp_for(task),
    )


def _split_strip_data(parent: Task, children: list[str]) -> TaskStripData:
    return TaskStripData(task_id=parent.id, title=parent.title, split_children=tuple(children))


def _strips(
    state: StateReader, focus_task: str | None, settings: Settings, width: int
) -> list[Text]:
    active = state.list_tasks(statuses=_ACTIVE_STATUSES)
    active.sort(key=lambda task: task.id != focus_task)
    rendered: list[Text] = []
    seen_parents: set[str] = set()
    for task in active:
        parent = state.get_task(task.parent_id) if task.parent_id is not None else None
        if parent is not None and parent.status_reason == "split" and parent.id not in seen_parents:
            seen_parents.add(parent.id)
            children = [
                sibling.id for sibling in state.list_tasks() if sibling.parent_id == parent.id
            ]
            split_data = _split_strip_data(parent, children)
            rendered.append(TaskStrip(split_data).render(width))
        rendered.append(TaskStrip(_strip_data(task, settings)).render(width))
    return rendered


class StripsPanel(Static):
    """Renders every active task's strip, in dispatch order, focused task first."""

    def __init__(self, context: WidgetContext) -> None:
        """Hold `context`; nothing here does I/O until `on_mount`."""
        super().__init__()
        self._widget_context = context

    async def on_mount(self) -> None:
        """Render the current strips, then refresh on every `StateReader.watch` change."""
        await self._refresh()
        self.run_worker(self._widget_context.state.watch(self._refresh), exclusive=True)

    async def on_resize(self, event: events.Resize) -> None:
        """Re-render at the new width: a narrower pane switches every strip to its one-line form."""
        del event
        await self._refresh()

    async def _refresh(self) -> None:
        width = self.size.width or STRIP_WIDE_MIN_COLUMNS
        strips = _strips(
            self._widget_context.state,
            self._widget_context.focus_task,
            self._widget_context.settings,
            width,
        )
        if not strips:
            self.update("no active tasks")
            return
        self.update(Text("\n\n").join(strips))


class StripsWidget:
    """The `strips` dashboard widget plugin (06 §5.4, §6.2)."""

    name = "strips"

    def create(self, context: WidgetContext) -> Widget:
        """Build the `StripsPanel` for `context`."""
        return StripsPanel(context)
