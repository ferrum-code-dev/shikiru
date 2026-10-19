"""Session selection for resumed process-per-turn agent sessions (03 §4.1)."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import TYPE_CHECKING

from shikiru.domain.ladder import pick_rung
from shikiru.domain.runs import Run, RunStatus
from shikiru.errors import InvariantError
from shikiru.paths import turns_dir
from shikiru.workspace.reports import load_turn_report

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.agents.contracts import Agent, Rung
    from shikiru.config.settings import Settings
    from shikiru.domain.tasks import Role, Task
    from shikiru.engine.agents import AgentPool
    from shikiru.store.runs import RunStore


@dataclass(frozen=True)
class SessionSelection:
    """The rung, resumable session and run row selected for one role turn."""

    rung: Rung
    session_id: str | None
    handoff: bool
    run: Run


class SessionManager:
    """Select and persist the session lineage for the next turn of a task role."""

    def __init__(
        self,
        *,
        runs: RunStore,
        agents: AgentPool,
        project_root: Path,
        settings: Settings,
    ) -> None:
        self._runs = runs
        self._agents = agents
        self._project_root = project_root
        self._settings = settings

    def session_for(self, task: Task, role: Role) -> SessionSelection:
        """Choose a session, resuming it across turns and same-agent rung changes (03 §4.1)."""
        rung = pick_rung(task, role, self._settings)
        current = self._runs.get_live(task.id, role)
        if current is not None and current.agent == rung.agent:
            self._runs.set_rung(current.id, rung)
            refreshed = self._runs.get_live(task.id, role)
            if refreshed is None:
                message = f"{task.id} {role.value} run disappeared during session selection"
                raise InvariantError(message)
            return SessionSelection(rung, refreshed.session_id, False, refreshed)

        if current is not None:
            self._runs.close(current.id)
        history_exists = self._has_reports(task.id)
        run = self._runs.start(
            task.id,
            role,
            rung,
            billing_declaration=self._agents.billing_declaration(rung.agent),
        )
        return SessionSelection(rung, None, history_exists, run)

    def record_session_started(
        self, *, run_id: int, agent: Agent, session_id: str, run_dir: Path, rung: Rung
    ) -> None:
        """Persist the session id and the command an operator can use to resume it."""
        command = shlex.join(agent.interactive_command(session_id, run_dir, rung))
        self._runs.set_session(run_id, session_id, command)

    def close_task(self, task_id: str) -> None:
        """Close every role session after its task ends."""
        for run in self._runs.for_task(task_id):
            if run.status is not RunStatus.CLOSED:
                self._runs.close(run.id)

    def _has_reports(self, task_id: str) -> bool:
        task_turns_dir = turns_dir(self._project_root, task_id)
        if not task_turns_dir.is_dir():
            return False
        return any(
            load_turn_report(turn_dir) is not None
            for turn_dir in task_turns_dir.iterdir()
            if turn_dir.is_dir()
        )
