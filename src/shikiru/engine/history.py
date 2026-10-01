"""Read and select the reports recorded for a task's turns."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from shikiru.domain.reports import (
    DECISION_ACCEPT,
    ImplementationReport,
    PlanReport,
    Report,
    Verdict,
)
from shikiru.engine.integration import IntegrationMetadata
from shikiru.engine.turns import read_turn_report
from shikiru.paths import turns_dir
from shikiru.view_log import ordered_turn_dirs
from shikiru.workspace.reports import InvalidTurnReportError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from shikiru.domain.tasks import Task

_LOGGER = logging.getLogger(__name__)

ENGINE_VERDICT_FILENAME = "engine-verdict.json"
"""The engine verdict replacing an accept on a dirty workspace (03 §6)."""


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
            engine_verdict = turn_dir / ENGINE_VERDICT_FILENAME
            if engine_verdict.is_file():
                history.append(
                    (turn_dir.name, Verdict.model_validate_json(engine_verdict.read_text()))
                )
        return history

    @staticmethod
    def context(history: Sequence[tuple[str, Report]]) -> list[dict[str, str]]:
        """Serialize reports into the ordered prompt and escalation context."""
        return [{"label": label, "json": report.model_dump_json()} for label, report in history]

    @staticmethod
    def latest_question(history: Sequence[tuple[str, Report]]) -> str:
        """Return the last Implementer question, if the latest report has one."""
        if not history:
            return ""
        _, last = history[-1]
        return last.question if isinstance(last, ImplementationReport) else ""

    def latest_report(self, task_id: str) -> Report | None:
        """Return the newest valid report for `task_id`."""
        history = self.load(task_id)
        return history[-1][1] if history else None

    def integration_metadata(self, task: Task) -> IntegrationMetadata:
        """Choose the accepted summary, falling back to the latest plan or task title."""
        history = self.load(task.id)
        for _, report in reversed(history):
            if isinstance(report, Verdict) and report.decision == DECISION_ACCEPT:
                return IntegrationMetadata(
                    summary=report.summary,
                    residual_risks=tuple(report.residual_risks),
                )
        for _, report in reversed(history):
            if isinstance(report, PlanReport):
                return IntegrationMetadata(summary=report.summary)
        return IntegrationMetadata(summary=task.title)
