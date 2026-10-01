"""Acceptance of a completed turn's report: a dirty worktree cannot end a task's work (03 §2-3)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shikiru.domain.reports import (
    DECISION_ACCEPT,
    DECISION_CHANGES_REQUESTED,
    DECISION_SUPERSEDE,
    SEVERITY_BLOCKER,
    Issue,
    Verdict,
)
from shikiru.engine.events import emit_turn_completed, emit_verdict
from shikiru.engine.history import ENGINE_VERDICT_FILENAME
from shikiru.private import write_private_text

if TYPE_CHECKING:
    from shikiru.domain.steps import Outcome
    from shikiru.domain.tasks import Task
    from shikiru.engine.task_facts import TaskFacts
    from shikiru.engine.turn_execution import TurnExecution
    from shikiru.store.events import EventLog

_WORK_ENDING_DECISIONS = frozenset({DECISION_ACCEPT, DECISION_SUPERSEDE})
UNCOMMITTED_SUMMARY = "The workspace has uncommitted changes, so it cannot be integrated."
UNCOMMITTED_ISSUE = (
    "Uncommitted changes in the workspace: {paths}. "
    "Commit what belongs to this task and discard the rest."
)


class AcceptanceGuard:
    """Records a completed turn and rejects an accepting verdict over a dirty worktree."""

    def __init__(self, *, events: EventLog, facts: TaskFacts) -> None:
        """Record through `events` and inspect the worktree through `facts`."""
        self._events = events
        self._facts = facts

    async def settle[R: Outcome](
        self, task: Task, report: R, execution: TurnExecution
    ) -> R | Verdict:
        """Record the completed turn; return `report`, or the engine's rejection of a dirty accept.

        An `accept` or `supersede` verdict ends the task's work, so uncommitted changes in its
        worktree replace it with a `changes_requested` verdict written beside the turn's report.
        """
        emit_turn_completed(self._events, task, report, execution)
        if not isinstance(report, Verdict) or report.decision not in _WORK_ENDING_DECISIONS:
            return report
        facts = await self._facts.collect(task)
        if not facts.status:
            return report
        issue = Issue(
            severity=SEVERITY_BLOCKER,
            description=UNCOMMITTED_ISSUE.format(
                paths=", ".join(line.strip() for line in facts.status)
            ),
        )
        rejection = Verdict(
            decision=DECISION_CHANGES_REQUESTED, summary=UNCOMMITTED_SUMMARY, issues=[issue]
        )
        write_private_text(
            execution.turn_dir / ENGINE_VERDICT_FILENAME, rejection.model_dump_json(indent=2)
        )
        emit_verdict(self._events, task, rejection, rejection.summary)
        return rejection
