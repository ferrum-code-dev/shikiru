"""`EscalationRaiser`: builds the packet and records the escalation (docs/design/03-workflow.md §8).

The packet keeps an agent's product alternatives separate from the engine's allowed resolution
actions. Only the latter are accepted by the resolution command contract.
"""

from __future__ import annotations

import logging
import os
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from shikiru.domain.escalation import (
    ESCALATION_CAUSES,
    RESOLUTION_CONSEQUENCES,
    EscalationOption,
    options_for,
)
from shikiru.domain.escalation_evidence import NO_AGENT_PROPOSAL
from shikiru.domain.events import EventKind
from shikiru.domain.reports import (
    DECISION_ESCALATE,
    REPORT_JSON_INDENT,
    ConsultAnswer,
    EscalationRequest,
    Verdict,
)
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import EscalationReason
from shikiru.paths import run_dir
from shikiru.private import make_private_dir, write_private_text

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from contextlib import AbstractContextManager

    from shikiru.domain.escalation import Escalation
    from shikiru.domain.tasks import Step, Task
    from shikiru.engine.evidence import EvidenceCollector
    from shikiru.engine.titles import TaskTitles
    from shikiru.engine.transitions import TransitionWriter
    from shikiru.store.escalations import EscalationStore
    from shikiru.store.events import EventLog
    from shikiru.store.runs import RunStore

ESCALATIONS_DIR_NAME = "escalations"
PACKET_FILENAME_SUFFIX = ".md"
PACKET_TEMPORARY_SUFFIX = ".tmp"
LOST_ESCALATION_QUESTION = (
    "The task was escalated but its escalation record was lost; retry or cancel."
)
LOST_ESCALATION_ATTEMPTED = (
    "The engine restarted and found the task escalated with no open escalation."
)
RESTORED_PACKET_ATTEMPTED = "The packet file was missing and was rewritten from recorded state."
_LOGGER = logging.getLogger(__name__)
_REPORT_DATA = TypeAdapter(dict[str, object])
_REPORT_DECISION_KEY = "decision"
_VERDICT_SUMMARY_KEY = "summary"
_CONSULT_ANSWER_KEY = "answer"


@dataclass(frozen=True)
class PacketContext:
    """Everything `render_packet` needs to assemble one escalation packet (03 §8)."""

    task_id: str
    step: Step | None
    reason: EscalationReason
    timeline: Sequence[str]
    attempted: str
    reports: str
    facts: str
    view_tail: str
    sessions: str
    question: str
    options: tuple[EscalationOption, ...]
    agent_request: EscalationRequest | None = None


def _headline(context: PacketContext) -> str:
    step_label = context.step.value if context.step is not None else "no step"
    sentence = ESCALATION_CAUSES[context.reason]
    return f"{context.task_id} · {step_label} · {context.reason.value}: {sentence}"


def _options_section(options: tuple[EscalationOption, ...]) -> str:
    lines: list[str] = []
    for option in options:
        marker = " ★" if option.recommended else ""
        lines.append(f"- `{option.label.value}`{marker}: {RESOLUTION_CONSEQUENCES[option.label]}")
    return "\n".join(lines)


def _agent_request_from_reports(reports: Sequence[dict[str, str]]) -> EscalationRequest | None:
    if not reports:
        return None
    data = _REPORT_DATA.validate_json(reports[-1]["json"])
    if data.get(_REPORT_DECISION_KEY) != DECISION_ESCALATE:
        return None
    if _VERDICT_SUMMARY_KEY in data:
        report = Verdict.model_validate(data)
    elif _CONSULT_ANSWER_KEY in data:
        report = ConsultAnswer.model_validate(data)
    else:
        return None
    return report.escalation


def render_packet(context: PacketContext) -> str:
    """Render `context` as the packet markdown (03 §8).

    Headline, timeline, what was tried, evidence, the Leader's question and typed agent
    alternatives, then the engine's resolvable options. Each option set has one recommendation.
    """
    timeline = "\n".join(f"- {line}" for line in context.timeline) or "(no recorded events)"
    agent_alternatives = (
        context.agent_request.model_dump_json(indent=REPORT_JSON_INDENT)
        if context.agent_request
        else NO_AGENT_PROPOSAL
    )
    return (
        f"# {_headline(context)}\n\n"
        f"## Timeline\n\n{timeline}\n\n"
        f"## What was tried\n\n{context.attempted}\n\n"
        f"## Evidence\n\n"
        f"### Reports\n\n{context.reports or '(none)'}\n\n"
        f"### Facts\n\n{context.facts or '(none)'}\n\n"
        f"### Recent turn signals\n\n{context.view_tail or '(none)'}\n\n"
        f"### Sessions\n\n{context.sessions or '(none)'}\n\n"
        f"## Question\n\n{context.question}\n\n"
        f"## Agent alternatives\n\n"
        f"{agent_alternatives}\n\n"
        f"## Options\n\n{_options_section(context.options)}\n"
    )


class EscalationRaiser:
    """Builds the escalation packet and commits the escalated state with its row and event."""

    def __init__(
        self,
        escalations: EscalationStore,
        events: EventLog,
        runs: RunStore,
        project_root: Path,
        *,
        atomically: Callable[[], AbstractContextManager[object]],
        titles: TaskTitles,
        evidence: EvidenceCollector,
    ) -> None:
        """Persist through `escalations`/`events` in `atomically`, refresh titles via `titles`.

        `evidence` supplies the recorded reports, facts and turn tail of a restored packet.
        """
        self._escalations = escalations
        self._events = events
        self._runs = runs
        self._project_root = project_root
        self._atomically = atomically
        self._titles = titles
        self._evidence = evidence
        self._unwritable: set[Path] = set()

    async def raise_escalation(
        self,
        task: Task,
        *,
        step: Step | None,
        reason: EscalationReason,
        question: str,
        attempted: str,
        reports: Sequence[dict[str, str]],
        facts: str,
        view_tail: str,
        apply_state: Callable[[], Task],
        agent_request: EscalationRequest | None = None,
    ) -> Escalation:
        """Write the packet, then commit `apply_state`, the escalation row and its event as one.

        `apply_state` persists the task's escalated state and returns the task as stored. The
        packet file is written first and removed again if the transaction fails, so an escalated
        task never lacks its escalation. A packet that cannot be written does not fail the
        escalation: the row commits and `restore_packet` rewrites the file once the cause is gone
        (03 §8). Titles refresh after the commit.
        """
        options = options_for(reason)
        context = PacketContext(
            task_id=task.id,
            step=step,
            reason=reason,
            timeline=self._timeline(task.id),
            attempted=attempted,
            reports=self._reports_evidence(reports),
            facts=facts,
            view_tail=view_tail,
            sessions=self._sessions_evidence(task.id),
            question=question,
            options=options,
            agent_request=agent_request,
        )
        headline = _headline(context)
        escalation_id = self._escalations.reserve_id()
        packet_path = self._packet_path(task.id, escalation_id)
        self._write_packet(task.id, packet_path, context)
        try:
            with self._atomically():
                updated = apply_state()
                escalation = self._escalations.open(
                    task.id,
                    escalation_id=escalation_id,
                    step=step,
                    reason=reason,
                    headline=headline,
                    packet_path=str(packet_path),
                    options=options,
                )
                self._events.append(
                    EventKind.ESCALATION,
                    task_id=task.id,
                    message=headline,
                    data={"escalation_id": escalation.id, "reason": reason.value},
                )
        except BaseException:
            packet_path.unlink(missing_ok=True)
            raise
        await self._refresh_titles(updated)
        return escalation

    async def ensure_actionable(self, task: Task) -> None:
        """Give an escalated `task` an open escalation with a packet on disk (03 §7).

        A missing packet is rewritten from durable state at its recorded path; a task without
        any open escalation gets a new `engine_error` one. Neither duplicates an open escalation.
        """
        open_ones = [e for e in self._escalations.list_open() if e.task_id == task.id]
        if not open_ones:
            await self.raise_escalation(
                task,
                step=task.step,
                reason=EscalationReason.ENGINE_ERROR,
                question=LOST_ESCALATION_QUESTION,
                attempted=LOST_ESCALATION_ATTEMPTED,
                reports=(),
                facts="",
                view_tail="",
                apply_state=lambda: task,
            )
            return
        for escalation in self._without_packet(open_ones):
            path = Path(escalation.packet_path)
            if self._packet_directory_ready(task.id, path):
                context = await self._restored_context(task, escalation)
                self._write_packet(task.id, path, context)

    @staticmethod
    def _without_packet(escalations: Sequence[Escalation]) -> list[Escalation]:
        return [e for e in escalations if not Path(e.packet_path).exists()]

    async def _restored_context(self, task: Task, escalation: Escalation) -> PacketContext:
        evidence = await self._evidence.collect(task, escalation.step)
        return PacketContext(
            task_id=task.id,
            step=escalation.step,
            reason=escalation.reason,
            timeline=self._timeline(task.id),
            attempted=RESTORED_PACKET_ATTEMPTED,
            reports=self._reports_evidence(evidence.reports),
            facts=evidence.facts,
            view_tail=evidence.view_tail,
            sessions=self._sessions_evidence(task.id),
            question=escalation.headline,
            options=escalation.options,
            agent_request=_agent_request_from_reports(evidence.reports),
        )

    def _packet_directory_ready(self, task_id: str, path: Path) -> bool:
        try:
            make_private_dir(path.parent)
            if not os.access(path.parent, os.W_OK | os.X_OK):
                message = f"{path.parent} is not writable"
                raise PermissionError(message)
        except OSError as error:
            self._report_unwritable(task_id, path, error)
            return False
        return True

    def _write_packet(self, task_id: str, path: Path, context: PacketContext) -> None:
        try:
            make_private_dir(path.parent)
            temporary = path.with_name(path.name + PACKET_TEMPORARY_SUFFIX)
            try:
                write_private_text(temporary, render_packet(context))
                temporary.replace(path)
            except OSError:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)
                raise
        except OSError as error:
            self._report_unwritable(task_id, path, error)
        else:
            self._unwritable.discard(path)

    def _report_unwritable(self, task_id: str, path: Path, error: OSError) -> None:
        if path in self._unwritable:
            return
        self._unwritable.add(path)
        _LOGGER.error("escalation packet %s could not be written", path, exc_info=error)
        self._events.append(
            EventKind.ENGINE_FAULT,
            task_id=task_id,
            message=f"escalation packet {path.name} could not be written: {error}",
            data={"error": type(error).__name__, "recoverable": True, "packet": str(path)},
        )

    async def _refresh_titles(self, task: Task) -> None:
        try:
            await self._titles.refresh(task)
        except Exception:
            _LOGGER.exception("title refresh failed for %s", task.id)

    def _packet_path(self, task_id: str, escalation_id: str) -> Path:
        return (
            run_dir(self._project_root, task_id)
            / ESCALATIONS_DIR_NAME
            / (escalation_id + PACKET_FILENAME_SUFFIX)
        )

    def _timeline(self, task_id: str) -> list[str]:
        events = self._events.since(0, task_id=task_id)
        return [f"{event.ts.isoformat()} {event.kind.value}: {event.message}" for event in events]

    @staticmethod
    def _reports_evidence(reports: Sequence[dict[str, str]]) -> str:
        return "\n\n".join(f"**{report['label']}**\n\n{report['json']}" for report in reports)

    def _sessions_evidence(self, task_id: str) -> str:
        lines: list[str] = []
        for run in self._runs.for_task(task_id):
            resume = run.resume_command or "(not resumable)"
            lines.append(f"- {run.role.value}: {run.agent} session `{run.session_id}` — {resume}")
        return "\n".join(lines)


class DispatchFailureEscalator:
    """Escalate a task after repeated failures while preparing its dispatch."""

    def __init__(self, writer: TransitionWriter, raiser: EscalationRaiser) -> None:
        """Use `writer` for the state change and `raiser` for the escalation."""
        self._writer = writer
        self._raiser = raiser

    async def __call__(self, task: Task, error: Exception) -> None:
        """Mark `task` escalated and record the dispatch error for the operator."""
        transition = Transition.escalate_current(task, EscalationReason.ENGINE_ERROR)
        await self._raiser.raise_escalation(
            task,
            step=transition.step,
            reason=EscalationReason.ENGINE_ERROR,
            question=str(error),
            attempted="Workspace or pane setup failed after scheduler retries.",
            reports=(),
            facts="",
            view_tail="",
            apply_state=lambda: self._writer.persist(task, transition, None),
        )
