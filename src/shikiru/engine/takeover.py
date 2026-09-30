"""Operator take over and hand back of one task role's session (03 §9)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.domain.commands import COMMAND_ID_KEY
from shikiru.domain.events import EventKind
from shikiru.domain.tasks import Hold, Role, Status, Task
from shikiru.engine.turns import turn_environment
from shikiru.errors import CommandRejectedError
from shikiru.paths import run_dir, task_root
from shikiru.workspace.messages import command_message_key

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from contextlib import AbstractContextManager
    from pathlib import Path

    from shikiru.engine.agents import AgentPool
    from shikiru.engine.layout_guard import LayoutGuard
    from shikiru.store.events import EventLog
    from shikiru.store.runs import RunStore
    from shikiru.store.tasks import TaskStore
    from shikiru.tmux.client import Tmux
    from shikiru.workspace.messages import OperatorMessages

HAND_BACK_MESSAGE = (
    "The Operator worked in this session since your last turn; take their changes into account."
)
_ACTIVE_STATUSES = (Status.RUNNING, Status.INTEGRATING)


class Takeover:
    """Moves a role's session between the engine's headless turns and the Operator's pane."""

    def __init__(
        self,
        *,
        tmux: Tmux,
        layout: LayoutGuard,
        agents: AgentPool,
        tasks: TaskStore,
        runs: RunStore,
        messages: OperatorMessages,
        events: EventLog,
        project_root: Path,
        project_name: str,
        cancel_task: Callable[[str], Awaitable[None]],
        start_task: Callable[[str], Awaitable[None]],
        refresh_titles: Callable[[Task], Awaitable[None]],
        atomically: Callable[[], AbstractContextManager[object]],
    ) -> None:
        """Drive panes through `tmux`/`layout` and task flows through the two callbacks."""
        self._tmux = tmux
        self._layout = layout
        self._agents = agents
        self._tasks = tasks
        self._runs = runs
        self._messages = messages
        self._events = events
        self._project_root = project_root
        self._project_name = project_name
        self._cancel_task = cancel_task
        self._start_task = start_task
        self._refresh_titles = refresh_titles
        self._atomically = atomically

    async def take_over(self, task: Task, role: Role, command_id: int) -> None:
        """Stop `task`'s turn, hold it and run `role`'s session interactively in its pane.

        The run mark, hold and event (naming `command_id`) commit together before the pane is
        respawned; a replay after that commit skips them and only respawns the pane, which is
        repeatable, while the role is still taken over.
        """
        if not self._events.command_applied(EventKind.TAKEN_OVER, command_id):
            if self._runs.get_taken_over(task.id, role) is not None:
                message = f"{task.id} {role.value} is already taken over"
                raise CommandRejectedError(message)
            run = self._runs.get_live(task.id, role)
            if run is None or run.session_id is None:
                message = f"{task.id} has no {role.value} session to take over"
                raise CommandRejectedError(message)
            _role_pane(task, role)
            if task.status in _ACTIVE_STATUSES:
                await self._cancel_task(task.id)
            with self._atomically():
                self._runs.mark_taken_over(run.id)
                self._tasks.set_holds(task.id, self._current(task.id).holds | {Hold.INTERVENTION})
                self._events.append(
                    EventKind.TAKEN_OVER,
                    task_id=task.id,
                    message=f"{task.id} {role.value} taken over",
                    data={
                        "role": role.value,
                        "session_id": run.session_id,
                        COMMAND_ID_KEY: command_id,
                    },
                )
        owned = self._runs.get_taken_over(task.id, role)
        if owned is not None and owned.session_id is not None:
            await self._respawn_interactive(
                self._current(task.id), role, owned.agent, owned.session_id
            )
        await self._refresh_titles(self._current(task.id))

    async def _respawn_interactive(
        self, task: Task, role: Role, agent: str, session_id: str
    ) -> None:
        root = task_root(self._project_root, task.id)
        task_run_dir = run_dir(self._project_root, task.id)
        argv = self._agents.get(agent).interactive_command(session_id, task_run_dir)
        env = turn_environment(task, role, task_run_dir, self._project_name)
        await self._tmux.respawn_pane(_role_pane(task, role), argv, env=env, cwd=root)

    async def hand_back(self, task: Task, role: Role, command_id: int) -> None:
        """Return `role`'s taken-over session to the engine and re-run the current step.

        The run, hold and event changes commit together, and the event names `command_id`; a
        replay after that commit skips them and only repeats the follow-up.
        """
        if not self._events.command_applied(EventKind.HANDED_BACK, command_id):
            run = self._runs.get_taken_over(task.id, role)
            if run is None:
                message = f"{task.id} {role.value} is not taken over"
                raise CommandRejectedError(message)
            await self._layout.start_view(task.id, role, _role_pane(task, role))
            path = self._messages.queue(
                task.id, role, HAND_BACK_MESSAGE, command_message_key(command_id)
            )
            with self._atomically():
                self._runs.mark_live(run.id)
                if not self._runs.has_taken_over(task.id):
                    holds = self._current(task.id).holds - {Hold.INTERVENTION}
                    self._tasks.set_holds(task.id, holds)
                self._events.append(
                    EventKind.HANDED_BACK,
                    task_id=task.id,
                    message=f"{task.id} {role.value} handed back",
                    data={"role": role.value, "path": str(path), COMMAND_ID_KEY: command_id},
                )
        current = self._current(task.id)
        if current.status in _ACTIVE_STATUSES and not current.held:
            await self._start_task(task.id)
        await self._refresh_titles(self._current(task.id))

    def _current(self, task_id: str) -> Task:
        task = self._tasks.get(task_id)
        if task is None:
            message = f"task {task_id} disappeared during take over"
            raise CommandRejectedError(message)
        return task


def _role_pane(task: Task, role: Role) -> str:
    pane_id = task.panes.get(role)
    if pane_id is None:
        message = f"{task.id} has no {role.value} pane"
        raise CommandRejectedError(message)
    return pane_id
