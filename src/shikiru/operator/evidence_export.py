"""Read recorded task evidence and write a versioned local export."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from datetime import UTC
from typing import TYPE_CHECKING, Literal, Protocol

from shikiru.agents.contracts import COST_BASIS_UNKNOWN
from shikiru.config.settings import MAX_TIMELINE_PAGE_SIZE, MAX_TIMELINE_SIGNAL_SCAN_ROWS
from shikiru.domain.evidence_export import (
    EVIDENCE_EXPORT_FORMAT,
    EVIDENCE_EXPORT_VERSION,
    render_evidence_json,
    render_evidence_markdown,
)
from shikiru.domain.runs import has_positive_token_evidence
from shikiru.domain.timeline import TIMELINE_ALL_CATEGORY, TIMELINE_ENGINE_CATEGORY
from shikiru.errors import ShikiruError
from shikiru.operator.queries import aggregate_usage

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from shikiru.clock import Clock
    from shikiru.domain.runs import Run
    from shikiru.domain.tasks import Task
    from shikiru.operator.queries import (
        OperatorQueries,
        ReportRow,
        TaskTimelineEntry,
    )
    from shikiru.store.tasks import TaskRelationships

EvidenceFormat = Literal["markdown", "json"]
EXPORT_FORMAT_MARKDOWN = "markdown"
EXPORT_FORMAT_JSON = "json"
TIMELINE_ORDER = "newest_first"
TIMELINE_SCOPE_ENGINE = "engine_events_and_decisions"
TIMELINE_SCOPE_WITH_SIGNALS = "engine_events_and_decisions_and_normalized_signals"
TIMELINE_NO_PROGRESS = "task evidence timeline cursor did not advance"
RELATIONSHIPS_CHANGED = "task relationships changed while preparing the export; retry"
TASK_MISSING = "task no longer exists"
DESTINATION_REQUIRED = "choose a destination path"
COST_BASIS_BILLED = "billed"
COST_BASIS_API_EQUIVALENT = "api_equivalent"
USAGE_PROVENANCE_LEGACY = "legacy_recorded"


class EvidenceFileWriter(Protocol):
    """Local file boundary used by the read-only task evidence export use case."""

    def write(
        self,
        destination: str,
        content: str,
        *,
        overwrite: bool,
        cancelled: threading.Event,
    ) -> Path: ...


class TaskEvidenceExportError(ShikiruError):
    """A requested task evidence export cannot be assembled safely."""


@dataclass(frozen=True)
class TaskExportResult:
    """The completed local export destination and the evidence options used."""

    destination: Path
    format: EvidenceFormat
    signals_included: bool


class TaskEvidenceExporter:
    """Export one task's retained evidence without writing engine state."""

    def __init__(
        self,
        queries: OperatorQueries,
        writer: EvidenceFileWriter,
        clock: Clock,
    ) -> None:
        """Use the operator read queries, injected local writer and project clock."""
        self._queries = queries
        self._writer = writer
        self._clock = clock

    async def export(
        self,
        task_id: str,
        destination: str,
        *,
        export_format: EvidenceFormat,
        overwrite: bool,
        include_signals: bool,
    ) -> TaskExportResult:
        """Collect and write one selected task's recorded evidence."""
        if not destination.strip():
            raise ValueError(DESTINATION_REQUIRED)
        if export_format not in (EXPORT_FORMAT_MARKDOWN, EXPORT_FORMAT_JSON):
            message = f"unsupported evidence export format: {export_format}"
            raise ValueError(message)
        task = self._queries.get_task_state(task_id)
        if task is None:
            message = f"{TASK_MISSING}: {task_id}"
            raise TaskEvidenceExportError(message)
        runs = self._queries.task_runs(task_id)
        relationships = self._relationships(task_id)
        files = await self._queries.task_export_files(task_id)
        timeline = await self._timeline(task_id, include_signals=include_signals)
        if self._queries.get_task_state(task_id) is None:
            message = f"{TASK_MISSING}: {task_id}"
            raise TaskEvidenceExportError(message)
        exported_at = self._export_timestamp()
        unavailable = _unavailable_sections(
            files.reports,
            files.facts,
            files.errors,
            timeline,
            runs,
        )
        document = _document(
            task=task,
            relationships=relationships,
            reports=files.reports,
            facts=files.facts,
            timeline=timeline,
            runs=runs,
            unavailable=unavailable,
            exported_at=exported_at,
            include_signals=include_signals,
        )
        content = (
            render_evidence_markdown(document)
            if export_format == EXPORT_FORMAT_MARKDOWN
            else render_evidence_json(document)
        )
        cancelled = threading.Event()
        try:
            path = await asyncio.to_thread(
                self._writer.write,
                destination,
                content,
                overwrite=overwrite,
                cancelled=cancelled,
            )
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return TaskExportResult(path, export_format, include_signals)

    def _relationships(self, task_id: str) -> tuple[TaskRelationships, ...]:
        pages: list[TaskRelationships] = []
        offset = 0
        while True:
            page = self._queries.task_relationships(task_id, offset=offset)
            if page is None:
                message = f"{TASK_MISSING}: {task_id}"
                raise TaskEvidenceExportError(message)
            pages.append(page)
            if not page.has_more:
                _validate_relationship_counts(pages)
                return tuple(pages)
            next_offset = offset + page.page_size
            if page.page_size <= 0 or next_offset <= offset:
                raise TaskEvidenceExportError(TIMELINE_NO_PROGRESS)
            offset = next_offset

    async def _timeline(
        self,
        task_id: str,
        *,
        include_signals: bool,
    ) -> tuple[TaskTimelineEntry, ...]:
        category = TIMELINE_ALL_CATEGORY if include_signals else TIMELINE_ENGINE_CATEGORY
        entries: list[TaskTimelineEntry] = []
        cursor: str | None = None
        seen_cursors: set[str | None] = {cursor}
        while True:
            page = await self._queries.task_timeline(
                task_id,
                limit=MAX_TIMELINE_PAGE_SIZE,
                cursor=cursor,
                category=category,
                scan_limit=MAX_TIMELINE_SIGNAL_SCAN_ROWS,
            )
            if page is None:
                message = f"{TASK_MISSING}: {task_id}"
                raise TaskEvidenceExportError(message)
            entries.extend(page.entries)
            if not page.has_older:
                return tuple(entries)
            cursor = page.older_cursor
            if cursor is None or cursor in seen_cursors:
                raise TaskEvidenceExportError(TIMELINE_NO_PROGRESS)
            seen_cursors.add(cursor)

    def _export_timestamp(self) -> str:
        now = self._clock.now()
        if now.tzinfo is None or now.utcoffset() is None:
            message = "export clock returned a timestamp without a timezone"
            raise TaskEvidenceExportError(message)
        return now.astimezone(UTC).isoformat()


def _document(
    *,
    task: Task,
    relationships: Sequence[TaskRelationships],
    reports: Sequence[ReportRow],
    facts: Sequence[tuple[str, str]],
    timeline: Sequence[TaskTimelineEntry],
    runs: Sequence[Run],
    unavailable: Sequence[dict[str, str]],
    exported_at: str,
    include_signals: bool,
) -> dict[str, object]:
    return {
        "format": EVIDENCE_EXPORT_FORMAT,
        "version": EVIDENCE_EXPORT_VERSION,
        "task_id": task.id,
        "exported_at": exported_at,
        "metadata": {
            "format": EVIDENCE_EXPORT_FORMAT,
            "version": EVIDENCE_EXPORT_VERSION,
            "task_id": task.id,
            "exported_at": exported_at,
            "timeline_order": TIMELINE_ORDER,
            "timeline_scope": (
                TIMELINE_SCOPE_WITH_SIGNALS if include_signals else TIMELINE_SCOPE_ENGINE
            ),
            "normalized_signals_included": include_signals,
            "relationship_pages_exhausted": True,
            "timeline_pages_exhausted": True,
        },
        "request": task.request,
        "outcome": {
            "status": task.status.value,
            "reason": task.status_reason.value if task.status_reason is not None else None,
            "summary": task.summary,
            "wait_reason": task.wait_reason,
            "holds": sorted(hold.value for hold in task.holds),
            "created_at": task.created_at.isoformat(),
            "started_at": task.started_at.isoformat() if task.started_at is not None else None,
            "finished_at": task.finished_at.isoformat() if task.finished_at is not None else None,
        },
        "task": {
            "title": task.title,
            "mode": task.mode.value if task.mode is not None else None,
            "step": task.step.value if task.step is not None else None,
            "complexity": task.complexity,
            "priority": task.priority,
            "parent_id": task.parent_id,
            "depends_on": list(task.depends_on),
            "rung_index": {role.value: rung for role, rung in task.rung_index.items()},
            "overrides": {
                role.value: {
                    "agent": rung.agent,
                    "model": rung.model,
                    "effort": rung.effort,
                }
                for role, rung in task.overrides.items()
            },
        },
        "relationships": _relationship_document(relationships),
        "reports": [
            {
                "turn": report.turn,
                "kind": report.kind.value,
                "source": report.source,
                "data": report.data.model_dump(mode="json"),
            }
            for report in reports
        ],
        "facts": [{"turn": turn, "content": content} for turn, content in facts],
        "timeline": [entry.model_dump(mode="json") for entry in timeline],
        "sessions": [_run_document(run) for run in runs],
        "usage": _usage_summary(runs),
        "unavailable_sections": list(unavailable),
    }


def _relationship_document(pages: Sequence[TaskRelationships]) -> dict[str, object]:
    first = pages[0]
    return {
        "family_parent": _task_reference(first.family_parent),
        "family_root": _task_reference(first.family_root),
        "family_children": [
            _task_reference(task) for page in pages for task in page.family_children
        ],
        "family_child_count": first.family_child_count,
        "dependencies": [_task_reference(task) for page in pages for task in page.dependencies],
        "dependency_count": first.dependency_count,
        "blocking_dependencies": [
            _task_reference(task) for page in pages for task in page.blocking_dependencies
        ],
        "blocking_dependency_count": first.blocking_dependency_count,
        "dependents": [_task_reference(task) for page in pages for task in page.dependents],
        "dependent_count": first.dependent_count,
        "replacements": [_task_reference(task) for page in pages for task in page.replacements],
        "replacement_count": first.replacement_count,
        "family_outcomes": [
            {"outcome": outcome, "count": count} for outcome, count in first.family_outcomes
        ],
    }


def _validate_relationship_counts(pages: Sequence[TaskRelationships]) -> None:
    first = pages[0]
    counts = (
        (first.family_child_count, sum(len(page.family_children) for page in pages)),
        (first.dependency_count, sum(len(page.dependencies) for page in pages)),
        (
            first.blocking_dependency_count,
            sum(len(page.blocking_dependencies) for page in pages),
        ),
        (first.dependent_count, sum(len(page.dependents) for page in pages)),
        (first.replacement_count, sum(len(page.replacements) for page in pages)),
    )
    if any(expected != observed for expected, observed in counts):
        raise TaskEvidenceExportError(RELATIONSHIPS_CHANGED)


def _task_reference(task: Task) -> dict[str, object]:
    return {
        "id": task.id,
        "title": task.title,
        "status": task.status.value,
        "reason": task.status_reason.value if task.status_reason is not None else None,
        "parent_id": task.parent_id,
    }


def _run_document(run: Run) -> dict[str, object]:
    token_provenance = _token_source(run)
    cost_source = _cost_source(run)
    context_source = _context_source(run)
    return {
        "id": run.id,
        "role": run.role.value,
        "agent": run.agent,
        "model": run.model,
        "effort": run.effort,
        "billing": {"mode": run.billing_mode, "source": run.billing_source},
        "session_id": run.session_id,
        "status": run.status.value,
        "started_at": run.started_at.isoformat(),
        "ended_at": run.ended_at.isoformat() if run.ended_at is not None else None,
        "usage_provenance": {
            "reported_marker": run.usage_reported,
            "tokens": token_provenance,
            "cost": cost_source,
            "context": context_source,
        },
        "tokens": {
            "input": run.usage.input_tokens if token_provenance != COST_BASIS_UNKNOWN else None,
            "output": run.usage.output_tokens if token_provenance != COST_BASIS_UNKNOWN else None,
            "cache_read": run.usage.cache_read_tokens
            if token_provenance != COST_BASIS_UNKNOWN
            else None,
            "cache_write": run.usage.cache_write_tokens
            if token_provenance != COST_BASIS_UNKNOWN
            else None,
        },
        "cost": {
            "usd": run.usage.cost_usd if cost_source != COST_BASIS_UNKNOWN else None,
            "basis": run.usage.cost_basis,
        },
        "context_used_pct": (
            run.usage.context_used_pct if context_source != COST_BASIS_UNKNOWN else None
        ),
    }


def _token_source(run: Run) -> str:
    if run.usage_reported is True:
        return "reported"
    if run.usage_reported is None and has_positive_token_evidence(run):
        return USAGE_PROVENANCE_LEGACY
    return COST_BASIS_UNKNOWN


def _cost_source(run: Run) -> str:
    if (
        run.usage.cost_usd is None
        or run.usage.cost_basis not in (COST_BASIS_BILLED, COST_BASIS_API_EQUIVALENT)
        or run.usage_reported is False
    ):
        return COST_BASIS_UNKNOWN
    return "reported" if run.usage_reported is True else USAGE_PROVENANCE_LEGACY


def _context_source(run: Run) -> str:
    if run.usage.context_used_pct is None or run.usage_reported is False:
        return COST_BASIS_UNKNOWN
    return "reported" if run.usage_reported is True else USAGE_PROVENANCE_LEGACY


def _usage_summary(runs: Sequence[Run]) -> list[dict[str, object]]:
    summaries: list[dict[str, object]] = []
    for usage in aggregate_usage(runs):
        token_run_count = usage.usage_reported_runs + usage.legacy_token_runs
        tokens: dict[str, int | None] = {
            "input": usage.input_tokens if token_run_count else None,
            "output": usage.output_tokens if token_run_count else None,
            "cache_read": usage.cache_read_tokens if token_run_count else None,
            "cache_write": usage.cache_write_tokens if token_run_count else None,
        }
        summaries.append(
            {
                "agent": usage.agent,
                "run_count": usage.run_count,
                "reported_token_runs": usage.usage_reported_runs,
                "legacy_token_runs": usage.legacy_token_runs,
                "unknown_token_runs": usage.run_count - token_run_count,
                "tokens": tokens,
                "billed_cost_usd": usage.billed_cost_usd,
                "billed_cost_runs": usage.billed_cost_runs,
                "legacy_billed_cost_runs": usage.legacy_billed_cost_runs,
                "api_equivalent_cost_usd": usage.api_equivalent_cost_usd,
                "api_equivalent_cost_runs": usage.api_equivalent_cost_runs,
                "legacy_api_equivalent_cost_runs": usage.legacy_api_equivalent_cost_runs,
                "unknown_cost_runs": usage.unknown_cost_runs,
            }
        )
    return summaries


def _unavailable_sections(
    reports: Sequence[ReportRow],
    facts: Sequence[tuple[str, str]],
    file_errors: Sequence[tuple[str, str]],
    timeline: Sequence[TaskTimelineEntry],
    runs: Sequence[Run],
) -> list[dict[str, str]]:
    return [
        *_missing_evidence_sections(reports, facts, timeline, runs),
        *({"section": section, "reason": error} for section, error in file_errors),
        *_timeline_unavailable_sections(timeline),
    ]


def _missing_evidence_sections(
    reports: Sequence[ReportRow],
    facts: Sequence[tuple[str, str]],
    timeline: Sequence[TaskTimelineEntry],
    runs: Sequence[Run],
) -> list[dict[str, str]]:
    unavailable: list[dict[str, str]] = []
    if not reports:
        unavailable.append(
            {
                "section": "reports",
                "reason": "No retained validated report or verdict is available.",
            }
        )
    if not facts:
        unavailable.append(
            {"section": "facts", "reason": "No retained git facts file is available."}
        )
    if not runs:
        unavailable.append(
            {"section": "sessions", "reason": "No recorded task session is available."}
        )
    if not timeline:
        unavailable.append(
            {"section": "timeline", "reason": "No retained task timeline entries are available."}
        )
    return unavailable


def _timeline_unavailable_sections(timeline: Sequence[TaskTimelineEntry]) -> list[dict[str, str]]:
    unavailable: list[dict[str, str]] = []
    for entry in timeline:
        if entry.identity.startswith("missing:"):
            unavailable.append(
                {
                    "section": "signals",
                    "reason": f"{entry.role or 'agent'} signal file is unavailable.",
                }
            )
        elif entry.identity.startswith("unavailable:"):
            unavailable.append({"section": "signals", "reason": entry.detail})
        elif entry.incomplete:
            unavailable.append(
                {
                    "section": "signals",
                    "reason": entry.timestamp_error or "A signal row is incomplete.",
                }
            )
        elif entry.timestamp_error is not None:
            unavailable.append({"section": "timeline", "reason": entry.timestamp_error})
    return unavailable
