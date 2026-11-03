"""Read and select the reports recorded for a task's turns."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from shikiru.domain.reports import (
    DECISION_ACCEPT,
    ConsultAnswer,
    FeedbackVerdict,
    ImplementationReport,
    PlanReport,
    Report,
    Verdict,
)
from shikiru.engine.integration import IntegrationMetadata
from shikiru.engine.turns import read_turn_report
from shikiru.paths import turns_dir
from shikiru.view_log import ordered_turn_dirs
from shikiru.workspace.reports import ENGINE_VERDICT_FILENAME as _ENGINE_VERDICT_FILENAME
from shikiru.workspace.reports import (
    InvalidTurnReportError,
    load_engine_verdict,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from shikiru.domain.tasks import Task

_LOGGER = logging.getLogger(__name__)
ENGINE_VERDICT_FILENAME = _ENGINE_VERDICT_FILENAME


class ReportHistory:
    """Load reports in turn order and derive the contexts used by task flows."""

    def __init__(self, project_root: Path) -> None:
        """Read reports from the project's task run directories."""
        self._project_root = project_root

    def load(self, task_id: str) -> list[tuple[str, Report]]:
        """Load valid reports for `task_id`, logging and omitting invalid reports."""
        turns_root = turns_dir(self._project_root, task_id)
        if not turns_root.is_dir():
            return []
        history: list[tuple[str, Report]] = []
        for turn_dir in ordered_turn_dirs(turns_root):
            try:
                report = read_turn_report(turn_dir)
            except InvalidTurnReportError:
                _LOGGER.exception("invalid report in %s", turn_dir)
                report = None
            if report is not None:
                history.append((turn_dir.name, report))
            try:
                engine_verdict = load_engine_verdict(turn_dir)
            except InvalidTurnReportError:
                _LOGGER.exception("invalid engine verdict in %s", turn_dir)
                engine_verdict = None
            if engine_verdict is not None:
                history.append(
                    (
                        turn_dir.name,
                        engine_verdict,
                    )
                )
        return history

    @staticmethod
    def context(history: Sequence[tuple[str, Report]]) -> list[dict[str, str]]:
        """Serialize reports into the ordered prompt and escalation context."""
        return [{"label": label, "json": report.model_dump_json()} for label, report in history]

    @staticmethod
    def latest_question(history: Sequence[tuple[str, Report]]) -> str:
        """Return the Implementer question behind the latest consult, if it has one.

        The Leader's answers and escalations follow the question's report, so they are skipped.
        """
        for _, report in reversed(history):
            if isinstance(report, ConsultAnswer):
                continue
            return report.question if isinstance(report, ImplementationReport) else ""
        return ""

    def latest_report(self, task_id: str) -> Report | None:
        """Return the newest valid report for `task_id`."""
        history = self.load(task_id)
        return history[-1][1] if history else None

    def integration_metadata(self, task: Task) -> IntegrationMetadata:
        """Choose the accepted summary, falling back to the latest plan or task title."""
        history = self.load(task.id)
        for _, report in reversed(history):
            if (
                isinstance(report, Verdict)
                and not isinstance(report, FeedbackVerdict)
                and report.decision == DECISION_ACCEPT
            ):
                return IntegrationMetadata(
                    summary=report.summary,
                    residual_risks=tuple(report.residual_risks),
                )
        for _, report in reversed(history):
            if isinstance(report, PlanReport):
                return IntegrationMetadata(summary=report.summary)
        return IntegrationMetadata(summary=task.title)
