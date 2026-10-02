"""Shared task detail presentation for the workspace and fixed task view."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, ClassVar

from rich.console import Group
from rich.text import Text
from textual.containers import HorizontalScroll, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from shikiru.domain.tasks import Role
from shikiru.errors import ShikiruError
from shikiru.ui.strip import TaskStrip

if TYPE_CHECKING:
    from textual.app import ComposeResult
    from textual.worker import Worker

    from shikiru.dashboard.contract import TaskDetailView, TaskReportView, WidgetContext

TASK_DETAIL_WORKER_GROUP = "task-detail-updates"
TASK_DETAIL_ESCAPE = "escape"
TASK_DETAIL_BACK_BUTTON = "task-detail-back"
TASK_DETAIL_WINDOW_BUTTON = "task-detail-window"
TASK_DETAIL_LEADER_BUTTON = "task-detail-leader"
TASK_DETAIL_IMPLEMENTER_BUTTON = "task-detail-implementer"
TASK_DETAIL_ACTIONS_BUTTON = "task-detail-actions"
TASK_DETAIL_ATTENTION_BUTTON = "task-detail-attention"
TASK_DETAIL_IDENTITY = "task-detail-identity"
TASK_DETAIL_SITUATION = "task-detail-situation"
TASK_DETAIL_PROGRESS = "task-detail-progress"
TASK_DETAIL_REQUEST = "task-detail-request"
TASK_DETAIL_CRITERIA = "task-detail-criteria"
TASK_DETAIL_REPORT = "task-detail-report"
TASK_DETAIL_VERDICT = "task-detail-verdict"
TASK_DETAIL_EVIDENCE = "task-detail-evidence"
TASK_DETAIL_FACTS = "task-detail-facts"
TASK_DETAIL_WORKSPACE = "task-detail-workspace"
TASK_DETAIL_SESSIONS = "task-detail-sessions"
TASK_DETAIL_FEEDBACK = "task-detail-feedback"
NO_VALUE = "\N{EN DASH}"
NO_REPORT = "No validated report is recorded."
NO_VERDICT = "No validated verdict is recorded."
NO_CRITERIA = "No validated acceptance criteria are recorded."
NO_EVIDENCE = "No recorded agent signals or task events are available."
NO_FACTS = "No engine-computed git facts are recorded."
PANEL_WIDTH_PADDING = 4


class TaskDetailPanel(Vertical):
    """Render shared task detail from the injected dashboard view contract."""

    DEFAULT_CSS = """
    #task-detail-panel { width: 100%; height: auto; }
    #task-detail-identity {
        height: auto; padding: 1; border-bottom: solid $neutral-border;
        background: $panel; color: $primary-text; text-style: bold;
    }
    #task-detail-toolbar { height: 3; padding: 0 1; }
    #task-detail-toolbar Button {
        width: auto; min-width: 1; margin-right: 1;
        border: round $neutral-border; background: $surface; color: $primary-text;
    }
    #task-detail-toolbar Button:focus { border: round $focus; text-style: bold reverse; }
    #task-detail-feedback { height: auto; min-height: 1; color: $secondary-text; }
    #task-detail-body { height: auto; max-height: 32; padding: 0 1; }
    .task-detail-section {
        height: auto; padding: 1; margin-bottom: 1;
        border: round $neutral-border; background: $surface; color: $primary-text;
    }
    .task-detail-fixed #task-detail-body { max-height: 24; }
    .task-detail-screen { height: 100%; }
    .task-detail-screen #task-detail-panel { height: 100%; }
    .task-detail-screen #task-detail-body { height: 1fr; max-height: 1fr; }
    """

    def __init__(
        self,
        context: WidgetContext,
        task_id: str,
        *,
        fixed: bool,
        close_detail: CloseDetail | None = None,
        classes: str = "",
    ) -> None:
        panel_classes = ["task-detail-fixed"] if fixed else []
        if classes:
            panel_classes.append(classes)
        super().__init__(id="task-detail-panel", classes=" ".join(panel_classes))
        self._widget_context = context
        self._task_id = task_id
        self._fixed = fixed
        self._close_detail = close_detail
        self._refresh_lock = asyncio.Lock()
        self._refresh_worker: Worker[None] | None = None
        self._change_worker: Worker[None] | None = None

    def compose(self) -> ComposeResult:
        with HorizontalScroll(id="task-detail-toolbar"):
            yield Button("Home" if self._fixed else "Back", id=TASK_DETAIL_BACK_BUTTON)
            yield Button("Task window", id=TASK_DETAIL_WINDOW_BUTTON)
            yield Button("Leader pane", id=TASK_DETAIL_LEADER_BUTTON)
            yield Button("Implementer pane", id=TASK_DETAIL_IMPLEMENTER_BUTTON)
            yield Button("Actions · Ctrl+P", id=TASK_DETAIL_ACTIONS_BUTTON)
            yield Button("Attention · Home", id=TASK_DETAIL_ATTENTION_BUTTON)
        yield Static("Loading task detail…", id=TASK_DETAIL_IDENTITY, markup=False)
        yield Static("", id=TASK_DETAIL_FEEDBACK, markup=False)
        with VerticalScroll(id="task-detail-body"):
            yield Static("", id=TASK_DETAIL_SITUATION, classes="task-detail-section", markup=False)
            yield Static("", id=TASK_DETAIL_PROGRESS, classes="task-detail-section")
            yield Static("", id=TASK_DETAIL_REQUEST, classes="task-detail-section", markup=False)
            yield Static("", id=TASK_DETAIL_CRITERIA, classes="task-detail-section", markup=False)
            yield Static("", id=TASK_DETAIL_REPORT, classes="task-detail-section", markup=False)
            yield Static("", id=TASK_DETAIL_VERDICT, classes="task-detail-section", markup=False)
            yield Static("", id=TASK_DETAIL_EVIDENCE, classes="task-detail-section", markup=False)
            yield Static("", id=TASK_DETAIL_FACTS, classes="task-detail-section", markup=False)
            yield Static("", id=TASK_DETAIL_WORKSPACE, classes="task-detail-section", markup=False)
            yield Static("", id=TASK_DETAIL_SESSIONS, classes="task-detail-section", markup=False)

    async def on_mount(self) -> None:
        self._refresh_worker = self.run_worker(self._refresh(), group=TASK_DETAIL_WORKER_GROUP)
        self._change_worker = self.run_worker(
            self._widget_context.on_change(self._refresh),
            group=TASK_DETAIL_WORKER_GROUP,
        )

    def on_unmount(self) -> None:
        if self._refresh_worker is not None:
            self._refresh_worker.cancel()
        if self._change_worker is not None:
            self._change_worker.cancel()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == TASK_DETAIL_BACK_BUTTON:
            await self._back()
        elif button_id == TASK_DETAIL_WINDOW_BUTTON:
            await self._navigate_task_window()
        elif button_id == TASK_DETAIL_LEADER_BUTTON:
            await self._navigate_role_pane(Role.LEADER)
        elif button_id == TASK_DETAIL_IMPLEMENTER_BUTTON:
            await self._navigate_role_pane(Role.IMPLEMENTER)
        elif button_id == TASK_DETAIL_ACTIONS_BUTTON:
            await self._widget_context.open_palette("")
        elif button_id == TASK_DETAIL_ATTENTION_BUTTON:
            await self._navigate_home()

    async def _back(self) -> None:
        if self._fixed:
            await self._navigate_home()
            return
        if self._close_detail is not None:
            await self._close_detail()

    async def _navigate_task_window(self) -> None:
        callback = self._widget_context.navigate_task_window
        await self._show_navigation_result(
            await callback(self._task_id)
            if callback is not None
            else "Task window navigation is unavailable."
        )

    async def _navigate_role_pane(self, role: Role) -> None:
        callback = self._widget_context.navigate_role_pane
        await self._show_navigation_result(
            await callback(self._task_id, role.value)
            if callback is not None
            else f"{role.value} pane navigation is unavailable."
        )

    async def _navigate_home(self) -> None:
        callback = self._widget_context.navigate_home
        await self._show_navigation_result(
            await callback()
            if callback is not None
            else "Home workspace navigation is unavailable."
        )

    async def _show_navigation_result(self, result: str) -> None:
        self.query_one(f"#{TASK_DETAIL_FEEDBACK}", Static).update(result)

    async def _refresh(self) -> None:
        async with self._refresh_lock:
            try:
                view = await self._widget_context.views.task_detail(self._task_id)
            except (ShikiruError, OSError, ValueError, TypeError) as error:
                if self.is_mounted:
                    self.query_one(f"#{TASK_DETAIL_FEEDBACK}", Static).update(
                        f"Task detail refresh failed: {error}"
                    )
                return
            if not self.is_mounted:
                return
            if view is None:
                self.query_one(f"#{TASK_DETAIL_IDENTITY}", Static).update(
                    f"{self._task_id} · task record is unavailable"
                )
                self.query_one(f"#{TASK_DETAIL_FEEDBACK}", Static).update(
                    "Task state could not be read."
                )
                return
            self._render_view(view)

    def _render_view(self, view: TaskDetailView) -> None:
        identity = (
            f"{view.task_id} · {view.title}\n"
            f"{view.status} · {view.step} · iteration {view.iteration}"
        )
        self.query_one(f"#{TASK_DETAIL_IDENTITY}", Static).update(identity)
        situation = [f"Situation · {view.status} · {view.step}"]
        if view.status_reason is not None:
            situation.append(f"Status reason · {view.status_reason}")
        if view.mode is not None:
            situation.append(f"Mode · {view.mode}")
        if view.holds:
            situation.append(f"Holds · {', '.join(view.holds)}")
        if view.wait_reason is not None:
            situation.append(f"Waiting · {view.wait_reason}")
        if view.ownership:
            situation.append(f"Operator owns · {', '.join(view.ownership)}")
        attention = view.attention
        situation.append(
            f"Global attention · {attention.decisions} decisions · {attention.faults} engine faults"
        )
        self.query_one(f"#{TASK_DETAIL_SITUATION}", Static).update("\n".join(situation))
        progress_width = max(self.size.width - PANEL_WIDTH_PADDING, 1)
        progress = TaskStrip(view.progress).render(progress_width)
        self.query_one(f"#{TASK_DETAIL_PROGRESS}", Static).update(
            Group(Text("Progress · actual task mode"), progress)
        )
        self.query_one(f"#{TASK_DETAIL_REQUEST}", Static).update(f"Request\n{view.request}")
        criteria = "\n".join(f"- {item}" for item in view.acceptance_criteria)
        self.query_one(f"#{TASK_DETAIL_CRITERIA}", Static).update(
            f"Acceptance criteria\n{criteria or NO_CRITERIA}"
        )
        self.query_one(f"#{TASK_DETAIL_REPORT}", Static).update(
            _report_text("Latest validated report", view.latest_report, NO_REPORT)
        )
        self.query_one(f"#{TASK_DETAIL_VERDICT}", Static).update(
            _report_text("Latest validated verdict", view.latest_verdict, NO_VERDICT)
        )
        self.query_one(f"#{TASK_DETAIL_EVIDENCE}", Static).update(_evidence_text(view))
        facts = (
            f"Engine-computed git facts · {view.facts_turn}\n{view.recorded_facts}"
            if view.recorded_facts is not None
            else NO_FACTS
        )
        self.query_one(f"#{TASK_DETAIL_FACTS}", Static).update(facts)
        self.query_one(f"#{TASK_DETAIL_WORKSPACE}", Static).update(_workspace_text(view))
        self.query_one(f"#{TASK_DETAIL_SESSIONS}", Static).update(_sessions_text(view))


class TaskDetailScreen(ModalScreen[None]):
    """Show a selected task's shared detail over the Home browser."""

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [(TASK_DETAIL_ESCAPE, "close", "Back")]
    DEFAULT_CSS = """
    TaskDetailScreen { width: 100%; height: 100%; background: $background; }
    """

    def __init__(self, context: WidgetContext, task_id: str) -> None:
        super().__init__()
        self._widget_context = context
        self._task_id = task_id

    def compose(self) -> ComposeResult:
        yield TaskDetailPanel(
            self._widget_context,
            self._task_id,
            fixed=False,
            close_detail=self._close,
            classes="task-detail-screen",
        )

    async def _close(self) -> None:
        self.dismiss()

    async def action_close(self) -> None:
        self.dismiss()


type CloseDetail = Callable[[], Awaitable[None]]


def _report_text(label: str, report: TaskReportView | None, missing: str) -> str:
    if report is None:
        return missing
    lines = [
        f"{label} · {report.kind} · {report.role} · {report.provenance} · {report.turn}",
        report.summary,
    ]
    if report.decision is not None:
        lines.append(f"Decision · {report.decision}")
    if report.question:
        lines.append(f"Question · {report.question}")
    if report.issues:
        lines.append("Issues\n" + "\n".join(f"- {item}" for item in report.issues))
    if report.risks:
        lines.append("Risks\n" + "\n".join(f"- {item}" for item in report.risks))
    if report.tests:
        lines.append(
            "Agent-reported tests\n"
            + "\n".join(
                f"- {item.name} · {item.result} · {item.provenance}"
                + (f" · {item.details}" if item.details else "")
                for item in report.tests
            )
        )
    return "\n".join(lines)


def _evidence_text(view: TaskDetailView) -> str:
    sections = ["Recorded evidence"]
    if view.signals:
        sections.extend(
            f"{signal.role} agent signals\n" + "\n".join(signal.lines) for signal in view.signals
        )
    else:
        sections.append(NO_EVIDENCE)
    if view.events:
        sections.append(
            "Recent engine events\n"
            + "\n".join(
                f"{event.timestamp} · {event.kind} · {event.message}" for event in view.events
            )
        )
    if view.evidence_errors:
        sections.append(
            "Evidence unavailable\n" + "\n".join(f"- {item}" for item in view.evidence_errors)
        )
    return "\n\n".join(sections)


def _workspace_text(view: TaskDetailView) -> str:
    lines = ["Workspace and existing targets"]
    lines.append(f"Branch · {view.branch or NO_VALUE}")
    lines.append(f"Recorded worktree path · {view.worktree_path}")
    lines.append(f"Recorded task window ID · {view.window_id or NO_VALUE}")
    lines.extend(
        f"{pane.role.title()} pane ID · {pane.recorded_target or NO_VALUE}"
        for pane in view.role_panes
    )
    return "\n".join(lines)


def _sessions_text(view: TaskDetailView) -> str:
    lines = ["Task-scoped usage and session metadata"]
    if not view.sessions:
        lines.append("No agent sessions are recorded.")
    for session in view.sessions:
        cost = (
            NO_VALUE
            if session.cost_usd is None
            else f"${session.cost_usd:.2f} · {session.cost_basis}"
        )
        context = (
            NO_VALUE if session.context_used_pct is None else f"{session.context_used_pct:.0f}%"
        )
        effort = f"/{session.effort}" if session.effort else ""
        lines.extend(
            (
                (
                    f"{session.role.title()} · {session.agent}:{session.model}{effort} · "
                    f"{session.status}"
                ),
                f"Session ID · {session.session_id or NO_VALUE}",
                (
                    f"Tokens · input {session.input_tokens} · output {session.output_tokens} · "
                    f"cache read {session.cache_read_tokens} · "
                    f"cache write {session.cache_write_tokens}"
                ),
                f"Agent-reported cost · {cost} · context · {context}",
            )
        )
    return "\n".join(lines)
