"""Gathers the evidence of a task escalation: reports, workspace facts and the turn tail (03 §8)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from shikiru.domain.steps import ACTING_ROLE
from shikiru.errors import ShikiruError
from shikiru.paths import run_dir
from shikiru.view_log import read_tail_lines, view_log_path

if TYPE_CHECKING:
    from pathlib import Path

    from shikiru.domain.tasks import Role, Step, Task
    from shikiru.engine.history import ReportHistory
    from shikiru.engine.task_facts import TaskFacts

_LOGGER = logging.getLogger(__name__)

ESCALATION_PANE_TAIL_LINES = 60
"""Lines of pane scrollback captured as escalation evidence (03 §8)."""

FACTS_UNAVAILABLE = "(facts unavailable: {error})"
HISTORY_UNAVAILABLE = "(report history unavailable, so no reports are shown: {error})"


@dataclass(frozen=True)
class Evidence:
    """The recorded reports, rendered facts and recent turn signals of one task."""

    reports: list[dict[str, str]]
    facts: str
    view_tail: str


class EvidenceCollector:
    """Collects the evidence that still exists for a task from its files and git."""

    def __init__(self, *, history: ReportHistory, facts: TaskFacts, project_root: Path) -> None:
        """Read reports through `history`, facts through `facts` and turn tails under the root."""
        self._history = history
        self._facts = facts
        self._project_root = project_root

    async def collect(self, task: Task, step: Step | None, role: Role | None = None) -> Evidence:
        """Gather `task`'s evidence; the turn tail is that of `role` or of `step`'s acting role."""
        history_note = ""
        try:
            history = self._history.load(task.id)
        except (OSError, ValueError) as error:
            _LOGGER.exception("could not load report history for escalation of %s", task.id)
            history = []
            history_note = HISTORY_UNAVAILABLE.format(error=error)
        facts = await self._workspace_facts(task)
        return Evidence(
            reports=self._history.context(history),
            facts="\n\n".join(part for part in (history_note, facts) if part),
            view_tail=self._view_tail(task, step, role),
        )

    async def _workspace_facts(self, task: Task) -> str:
        if task.repo is None:
            return ""
        try:
            return await self._facts.text(task)
        except ShikiruError as error:
            return FACTS_UNAVAILABLE.format(error=error)

    def _view_tail(self, task: Task, step: Step | None, role_override: Role | None) -> str:
        role = role_override
        if role is None and step is not None:
            role = ACTING_ROLE[step]
        if role is None:
            return ""
        path = view_log_path(run_dir(self._project_root, task.id), role.value)
        if not path.is_file():
            return ""
        return "\n".join(read_tail_lines(path, ESCALATION_PANE_TAIL_LINES))
