"""Task window creation and periodic pane restoration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from shikiru.config.settings import LayoutSplit, PaneLeaf
from shikiru.domain.commands import HandBack
from shikiru.domain.events import EventKind
from shikiru.domain.tasks import ACTIVE_STATUSES, Role, Task
from shikiru.errors import InvariantError
from shikiru.paths import task_root
from shikiru.private import make_private_dir
from shikiru.tmux.client import (
    FORMAT_LAYOUT_PANE,
    FORMAT_PANE_DEAD,
    FORMAT_PANE_ID,
    FORMAT_WINDOW_ID,
    LAYOUT_PANE_OPTION,
    PANE_DEAD_FLAG,
)
from shikiru.tmux.decorator import render_title
from shikiru.view_log import view_argv

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from shikiru.config.settings import Settings
    from shikiru.engine.fault_reporter import FaultReporter
    from shikiru.store.commands import CommandQueue
    from shikiru.store.events import EventLog
    from shikiru.store.runs import RunStore
    from shikiru.store.tasks import TaskStore
    from shikiru.tmux.client import Tmux
    from shikiru.tmux.decorator import PaneDecorator
    from shikiru.tmux.layout import LayoutBuilder
    from shikiru.tmux.session import SessionView

_SHELL_ARGV = ("/bin/sh",)
_ROLE_PANE_NAMES = frozenset((Role.LEADER.value, Role.IMPLEMENTER.value))
_SHELL_PANE_NAME = "shell"
_LAYOUT_PANE_SEPARATOR = "\t"
_LAYOUT_PANE_FORMAT = f"{FORMAT_PANE_ID}{_LAYOUT_PANE_SEPARATOR}{FORMAT_LAYOUT_PANE}"
_SNAPSHOT_PANE_FORMAT = _LAYOUT_PANE_SEPARATOR.join(
    (FORMAT_WINDOW_ID, FORMAT_PANE_ID, FORMAT_PANE_DEAD, FORMAT_LAYOUT_PANE)
)
_SNAPSHOT_FIELD_COUNT = 4


@dataclass(frozen=True)
class _PaneRow:
    window_id: str
    pane_id: str
    dead: bool
    layout_name: str


def _parse_snapshot(lines: list[str]) -> list[_PaneRow]:
    rows: list[_PaneRow] = []
    for line in lines:
        window_id, pane_id, dead, layout_name = line.split(
            _LAYOUT_PANE_SEPARATOR, _SNAPSHOT_FIELD_COUNT - 1
        )
        rows.append(_PaneRow(window_id, pane_id, dead == PANE_DEAD_FLAG, layout_name))
    return rows


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
        faults: FaultReporter,
        refresh_titles: Callable[[Task], Awaitable[None]],
    ) -> None:
        """Create windows through `tmux`, storing pane ids through `tasks`.

        A taken-over role's pane exiting is queued as a `HandBack` on `commands`. A task
        whose restoration fails is recorded through `faults` without stopping the others. Role pane
        titles are re-applied through `refresh_titles` whenever role panes are created or rebuilt.
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
        self._faults = faults
        self._refresh_titles = refresh_titles
        self._layout_pane_names = _layout_pane_names(settings.dashboard.layout)
        self._special_pane_names = tuple(
            name for name in self._layout_pane_names if name not in _ROLE_PANE_NAMES
        )
        self._special_panes: dict[str, dict[str, str]] = {}
        self._windows = asyncio.Lock()

    async def ensure(self, task: Task) -> Task:
        """Create `task`'s window or restore missing panes, returning fresh task state."""
        if task.window_id is None or not await self._tmux.target_exists(task.window_id):
            created = await self._create(task)
            return await self._refresh_cache(created.id)
        missing_agents = await self._missing_agent_roles(task)
        missing_special = await self._missing_special_panes(task)
        if missing_agents or missing_special:
            await self._restore_missing_panes(task, missing_agents)
        await self._restore_special_panes(task)
        restored = await self._refresh_cache(task.id)
        self._release_lost_takeovers(task.id, missing_agents)
        await self._restore_view_panes(restored)
        if missing_agents:
            self._record_restored(task.id, "agent")
        for pane in missing_special:
            self._record_restored(task.id, pane)
        return restored

    async def restore(self, tasks: list[Task]) -> None:
        """Restore the windows and dead panes of `tasks` that are still active when reached.

        A task that cannot be restored is recorded as an `engine_fault` and skipped.
        """
        snapshot: list[_PaneRow] | None = None
        for task in tasks:
            async with self._faults.isolate("layout restore", task_id=task.id), self._windows:
                current = self._tasks.get(task.id)
                if current is None or current.status not in ACTIVE_STATUSES:
                    continue
                if snapshot is None:
                    snapshot = _parse_snapshot(
                        await self._tmux.list_session_panes(
                            self._session_target, _SNAPSHOT_PANE_FORMAT
                        )
                    )
                if self._is_healthy(current, snapshot):
                    continue
                snapshot = None
                await self.ensure(current)

    def _is_healthy(self, task: Task, snapshot: list[_PaneRow]) -> bool:
        if task.window_id is None:
            return False
        window_rows = [row for row in snapshot if row.window_id == task.window_id]
        if not window_rows:
            return False
        session_panes = {row.pane_id for row in snapshot}
        for role in (Role.LEADER, Role.IMPLEMENTER):
            if task.panes.get(role) not in session_panes:
                return False
        window_names = {row.layout_name for row in window_rows}
        if not all(name in window_names for name in self._special_pane_names):
            return False
        managed = set(task.panes.values())
        managed.update(
            row.pane_id for row in window_rows if row.layout_name in self._special_pane_names
        )
        return not any(row.dead and row.pane_id in managed for row in snapshot)

    async def close(self, task: Task) -> None:
        """Close a task window when its task reaches a terminal state."""
        async with self._windows:
            if task.window_id is not None and await self._tmux.target_exists(task.window_id):
                await self._view.close_task_window(task.window_id)
            self._special_panes.pop(task.id, None)

    async def _create(self, task: Task) -> Task:
        root = task_root(self._project_root, task.id)
        make_private_dir(root)
        fields = {"task_id": task.id, "title": task.title}
        window_name = render_title(self._settings.dashboard.titles.window, fields)
        base = await self._view.create_task_window(
            self._session_target,
            window_name=window_name,
            cwd=root,
        )
        window_id = await self._tmux.window_id(base)
        panes = await self._builder.build(base, self._settings.dashboard.layout, cwd=root)
        role_panes = {
            Role(name): pane_id for name, pane_id in panes.items() if name in _ROLE_PANE_NAMES
        }
        self._special_panes[task.id] = {
            name: pane_id for name, pane_id in panes.items() if name in self._special_pane_names
        }
        await self._decorate_special_panes(root, panes)
        await self._decorator.configure_formats(window_id)
        for role, pane_id in role_panes.items():
            await self.start_view(task.id, role, pane_id)
        self._tasks.set_window(task.id, window_id)
        self._tasks.set_panes(task.id, role_panes)
        self._release_lost_takeovers(task.id, set(role_panes))
        updated = self._tasks.get(task.id)
        if updated is None:
            message = f"{task.id} vanished while recording its task window"
            raise InvariantError(message)
        await self._refresh_titles(updated)
        return updated

    async def _refresh_cache(self, task_id: str) -> Task:
        refreshed = self._tasks.get(task_id)
        if refreshed is None:
            message = f"{task_id} vanished while refreshing its task window"
            raise InvariantError(message)
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
            raise InvariantError(message)
        special = self._special_panes.setdefault(task.id, {})
        pane_rows = await self._tmux.list_panes(task.window_id, _LAYOUT_PANE_FORMAT)
        window_panes = {row.split(_LAYOUT_PANE_SEPARATOR, 1)[0] for row in pane_rows}
        for name, pane_id in tuple(special.items()):
            if pane_id not in window_panes:
                del special[name]
        for row in pane_rows:
            pane_id, name = row.split(_LAYOUT_PANE_SEPARATOR, 1)
            if name in self._special_pane_names:
                special[name] = pane_id
        available = [
            pane_id
            for pane_id in window_panes
            if pane_id not in task.panes.values() and pane_id not in special.values()
        ]
        available.sort()
        for name in self._special_pane_names:
            if name not in special and available:
                special[name] = available.pop(0)
                await self._tmux.set_pane_option(special[name], LAYOUT_PANE_OPTION, name)
        return {name for name in self._special_pane_names if name not in special}

    async def _restore_missing_panes(self, task: Task, missing_agents: set[Role]) -> None:
        if task.window_id is None:
            message = f"{task.id} has no window while restoring panes"
            raise InvariantError(message)
        root = task_root(self._project_root, task.id)
        existing = {
            role.value: pane_id
            for role, pane_id in task.panes.items()
            if await self._tmux.pane_exists(pane_id)
        }
        special = self._special_panes.setdefault(task.id, {})
        existing.update(
            {
                name: pane_id
                for name, pane_id in special.items()
                if await self._tmux.pane_exists(pane_id)
            }
        )
        panes = await self._builder.restore_missing(
            self._settings.dashboard.layout, existing, cwd=root
        )
        await self._builder.restore_vertical_stack(
            task.window_id, self._settings.dashboard.layout, panes
        )
        role_panes = {Role(name): panes[name] for name in _ROLE_PANE_NAMES if name in panes}
        special.clear()
        special.update({name: panes[name] for name in self._special_pane_names})
        await self._decorate_special_panes(root, panes)
        for role in missing_agents:
            await self.start_view(task.id, role, role_panes[role])
        self._tasks.set_panes(task.id, role_panes)
        await self._refresh_titles(await self._refresh_cache(task.id))

    async def _restore_special_panes(self, task: Task) -> None:
        special = self._special_panes.get(task.id, {})
        root = task_root(self._project_root, task.id)
        for name, pane_id in special.items():
            if not await self._tmux.is_pane_dead(pane_id):
                continue
            await self._tmux.respawn_pane(pane_id, _SHELL_ARGV, env={}, cwd=root)
            await self._title_special_pane(name, pane_id, root)
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

    def _release_lost_takeovers(self, task_id: str, lost_roles: set[Role]) -> None:
        for role in sorted(lost_roles):
            if self._runs.get_taken_over(task_id, role) is not None and self._queue_hand_back(
                HandBack(task_id=task_id, role=role)
            ):
                self._events.append(
                    EventKind.TAKEOVER_LOST,
                    task_id=task_id,
                    message=f"{task_id} {role.value} takeover lost its pane; handing back",
                    data={"role": role.value},
                )

    def _queue_hand_back(self, command: HandBack) -> bool:
        if any(
            isinstance(queued.command, HandBack)
            and (queued.command.task_id, queued.command.role) == (command.task_id, command.role)
            for queued in self._commands.pending()
        ):
            return False
        self._commands.append(command)
        return True

    async def _decorate_special_panes(self, root: Path, panes: dict[str, str]) -> None:
        for name in self._special_pane_names:
            pane_id = panes[name]
            await self._tmux.set_pane_option(pane_id, LAYOUT_PANE_OPTION, name)
            await self._title_special_pane(name, pane_id, root)

    async def _title_special_pane(self, name: str, pane_id: str, root: Path) -> None:
        if name == _SHELL_PANE_NAME:
            await self._decorator.set_pane_title(
                pane_id, self._settings.dashboard.titles.shell, {"cwd": str(root)}
            )

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


def _layout_pane_names(node: LayoutSplit | PaneLeaf) -> tuple[str, ...]:
    if isinstance(node, PaneLeaf):
        return (node.pane,)
    return tuple(name for child in node.panes for name in _layout_pane_names(child))
