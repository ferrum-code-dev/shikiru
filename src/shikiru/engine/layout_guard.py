"""Task window creation and periodic pane restoration (06 §5.2)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.agents.contracts import view_argv
from shikiru.domain.commands import HandBack
from shikiru.domain.events import EventKind
from shikiru.domain.tasks import Role, Task
from shikiru.errors import ShikiruError
from shikiru.paths import task_root
from shikiru.tmux.client import FORMAT_PANE_ID
from shikiru.tmux.decorator import render_title

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.config.settings import Settings
    from shikiru.store.commands import CommandQueue
    from shikiru.store.events import EventLog
    from shikiru.store.runs import RunStore
    from shikiru.store.tasks import TaskStore
    from shikiru.tmux.client import Tmux
    from shikiru.tmux.decorator import PaneDecorator
    from shikiru.tmux.layout import LayoutBuilder
    from shikiru.tmux.session import SessionView

_SHELL_ARGV = ("/bin/sh",)
_LAYOUT_ROLE_NAMES = (Role.LEADER.value, Role.IMPLEMENTER.value)
_SPECIAL_PANE_NAMES = ("system", "shell")
_RESTORED_PANE_SIZE_PCT = 50


class LayoutGuard:
    """Keeps each task's tmux window and non-agent panes aligned with persisted state."""

    def __init__(
        self,
        *,
        tmux: Tmux,
        view: SessionView,
        builder: LayoutBuilder,
        decorator: PaneDecorator,
        session_target: str,
        tasks: TaskStore,
        settings: Settings,
        project_root: Path,
        events: EventLog,
        runs: RunStore,
        commands: CommandQueue,
    ) -> None:
        """Create windows through `tmux`, storing pane ids through `tasks`.

        A taken-over role's pane exiting is queued as a `HandBack` on `commands` (03 §9).
        """
        self._tmux = tmux
        self._session_target = session_target
        self._tasks = tasks
        self._settings = settings
        self._project_root = project_root
        self._events = events
        self._view = view
        self._builder = builder
        self._decorator = decorator
        self._runs = runs
        self._commands = commands
        self._special_panes: dict[str, dict[str, str]] = {}

    async def ensure(self, task: Task) -> Task:
        """Create `task`'s window or restore missing panes, returning fresh task state."""
        if task.window_id is None or not await self._tmux.target_exists(task.window_id):
            created = await self._create(task)
            return await self._refresh_cache(created.id)
        missing_agents = await self._missing_agent_roles(task)
        missing_special = await self._missing_special_panes(task)
        if missing_agents or missing_special:
            await self._restore_missing_panes(task, missing_agents, missing_special)
        await self._restore_special_panes(task)
        restored = await self._refresh_cache(task.id)
        await self._restore_view_panes(restored)
        if missing_agents:
            self._record_restored(task.id, "agent")
        for pane in missing_special:
            self._record_restored(task.id, pane)
        return restored

    async def restore(self, tasks: list[Task]) -> None:
        """Restore all active task windows and dead system/shell panes."""
        for task in tasks:
            await self.ensure(task)

    async def close(self, task: Task) -> None:
        """Close a task window when its task reaches a terminal state."""
        if task.window_id is not None and await self._tmux.target_exists(task.window_id):
            await self._view.close_task_window(task.window_id)
        self._special_panes.pop(task.id, None)

    async def _create(self, task: Task) -> Task:
        root = task_root(self._project_root, task.id)
        root.mkdir(parents=True, exist_ok=True)
        fields = {"task_id": task.id, "title": task.title}
        window_name = render_title(self._settings.dashboard.titles.window, fields)
        base = await self._view.create_task_window(
            self._session_target,
            window_name=window_name,
            cwd=root,
        )
        window_id = await self._tmux.window_id(base)
        panes = await self._builder.build(base, self._settings.dashboard.layout)
        role_panes = {
            Role(name): pane_id for name, pane_id in panes.items() if name in _LAYOUT_ROLE_NAMES
        }
        self._special_panes[task.id] = {
            name: pane_id for name, pane_id in panes.items() if name in _SPECIAL_PANE_NAMES
        }
        await self._decorator.configure_formats(window_id)
        for role, pane_id in role_panes.items():
            await self.start_view(task.id, role, pane_id)
        self._tasks.set_window(task.id, window_id)
        self._tasks.set_panes(task.id, role_panes)
        updated = self._tasks.get(task.id)
        if updated is None:
            message = f"{task.id} vanished while recording its task window"
            raise ShikiruError(message)
        return updated

    async def _refresh_cache(self, task_id: str) -> Task:
        refreshed = self._tasks.get(task_id)
        if refreshed is None:
            message = f"{task_id} vanished while refreshing its task window"
            raise ShikiruError(message)
        return refreshed

    async def _missing_agent_roles(self, task: Task) -> set[Role]:
        missing: set[Role] = set()
        for role in (Role.LEADER, Role.IMPLEMENTER):
            pane_id = task.panes.get(role)
            if pane_id is None or not await self._tmux.pane_exists(pane_id):
                missing.add(role)
        return missing

    async def _missing_special_panes(self, task: Task) -> set[str]:
        if task.window_id is None:
            message = f"{task.id} has no window while checking special panes"
            raise ShikiruError(message)
        special = self._special_panes.setdefault(task.id, {})
        window_panes = set(await self._tmux.list_panes(task.window_id, FORMAT_PANE_ID))
        for name, pane_id in tuple(special.items()):
            if pane_id not in window_panes:
                del special[name]
        available = [
            pane_id
            for pane_id in window_panes
            if pane_id not in task.panes.values() and pane_id not in special.values()
        ]
        available.sort()
        for name in _SPECIAL_PANE_NAMES:
            if name not in special and available:
                special[name] = available.pop(0)
        return {name for name in _SPECIAL_PANE_NAMES if name not in special}

    async def _restore_missing_panes(
        self, task: Task, missing_agents: set[Role], missing_special: set[str]
    ) -> None:
        if task.window_id is None:
            message = f"{task.id} has no window while restoring panes"
            raise ShikiruError(message)
        root = task_root(self._project_root, task.id)
        role_panes = dict(task.panes)
        special = self._special_panes.setdefault(task.id, {})
        for role in (Role.LEADER, Role.IMPLEMENTER):
            if role in missing_agents:
                role_panes[role] = await self._split_restored_pane(task.window_id, root)
                await self.start_view(task.id, role, role_panes[role])
        for name in _SPECIAL_PANE_NAMES:
            if name in missing_special:
                special[name] = await self._split_restored_pane(task.window_id, root)
        self._tasks.set_panes(task.id, role_panes)

    async def _split_restored_pane(self, window_id: str, root: Path) -> str:
        pane_ids = await self._tmux.list_panes(window_id, FORMAT_PANE_ID)
        if not pane_ids:
            message = f"{window_id} has no surviving pane for restoration"
            raise ShikiruError(message)
        return await self._tmux.split_window(
            pane_ids[0],
            axis=self._settings.dashboard.layout.split,
            size_pct=_RESTORED_PANE_SIZE_PCT,
            cwd=root,
        )

    async def _restore_special_panes(self, task: Task) -> None:
        special = self._special_panes.get(task.id, {})
        root = task_root(self._project_root, task.id)
        for name, pane_id in special.items():
            if not await self._tmux.is_pane_dead(pane_id):
                continue
            await self._tmux.respawn_pane(pane_id, _SHELL_ARGV, env={}, cwd=root)
            self._record_restored(task.id, name)

    async def _restore_view_panes(self, task: Task) -> None:
        for role, pane_id in task.panes.items():
            if not await self._tmux.is_pane_dead(pane_id):
                continue
            if self._runs.get_taken_over(task.id, role) is not None:
                self._queue_hand_back(HandBack(task_id=task.id, role=role))
                continue
            await self.start_view(task.id, role, pane_id)
            self._record_restored(task.id, role.value)

    def _queue_hand_back(self, command: HandBack) -> None:
        if all(queued.command != command for queued in self._commands.pending()):
            self._commands.append(command)

    async def start_view(self, task_id: str, role: Role, pane_id: str) -> None:
        """Run `role`'s streaming view of `task_id` in `pane_id`."""
        argv = view_argv(self._project_root, task_id, role.value)
        root = task_root(self._project_root, task_id)
        await self._tmux.respawn_pane(pane_id, argv, env={}, cwd=root)

    def _record_restored(self, task_id: str, pane: str) -> None:
        self._events.append(
            EventKind.PANE_RESTORED,
            task_id=task_id,
            message=f"{task_id} pane restored: {pane}",
            data={"pane": pane},
        )
