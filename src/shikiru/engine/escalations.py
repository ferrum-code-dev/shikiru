"""`EscalationRaiser`: builds the packet and records the escalation (docs/design/03-workflow.md §8).

The packet's *actionable* options are always `domain.escalation.options_for(reason)` — the
fixed `answer`/`retry`/`accept`/`cancel` actions the Operator resolves an escalation with, each
with a static consequence sentence and exactly one recommended by default. Whatever options a
Leader may have proposed as the *product* decision (an `EscalationRequest`'s own `Option`s) are
part of the caller-supplied `question` text instead: they are content for a human to read, not
another action set to choose from.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from shikiru.domain.commands import ResolutionKind
from shikiru.domain.escalation import EscalationOption, options_for
from shikiru.domain.events import EventKind
from shikiru.domain.steps import Transition
from shikiru.domain.tasks import EscalationReason
from shikiru.errors import ShikiruError
from shikiru.paths import run_dir

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from shikiru.domain.escalation import Escalation
    from shikiru.domain.tasks import Step, Task
    from shikiru.engine.titles import TaskTitles
    from shikiru.store.escalations import EscalationStore
    from shikiru.store.events import EventLog
    from shikiru.store.runs import RunStore
    from shikiru.store.tasks import TaskStore

ESCALATIONS_DIR_NAME = "escalations"
PACKET_FILENAME_SUFFIX = ".md"

_HEADLINE_SENTENCES: dict[EscalationReason, str] = {
    EscalationReason.DECISION_REQUIRED: "The Leader needs an Operator decision to continue.",
    EscalationReason.ITERATIONS_EXHAUSTED: "The final review round still requested changes.",
    EscalationReason.INTEGRATION_CONFLICT: "Rebase conflicts could not be resolved automatically.",
    EscalationReason.INTEGRATION_BLOCKED: "The main checkout stayed blocked past the wait budget.",
    EscalationReason.PR_CLOSED: "The pull request was closed without merging.",
    EscalationReason.AGENT_AUTH: "The agent's authentication failed.",
    EscalationReason.AGENT_ERROR: "The agent kept failing after every automatic retry.",
    EscalationReason.AGENT_STUCK: "The agent's screen stayed stuck after every automatic recovery.",
    EscalationReason.AGENT_PROTOCOL: "The turn never produced a report, even with a Helper rebuild",
    EscalationReason.LADDER_EXHAUSTED: "Every rung on the ladder was tried without success.",
    EscalationReason.QUOTA_EXHAUSTED: "The agent's quota stayed exhausted past the wait budget.",
    EscalationReason.DEPENDENCY_CANCELLED: "A dependency of this task was cancelled.",
    EscalationReason.ENGINE_ERROR: "The engine hit an unexpected error while running this task.",
}

_CONSEQUENCES: dict[ResolutionKind, str] = {
    ResolutionKind.ANSWER: "Give the Leader guidance; the task continues from a consult turn.",
    ResolutionKind.RETRY: "Re-run the step that raised this escalation.",
    ResolutionKind.ACCEPT: "Integrate the work as it stands.",
    ResolutionKind.CANCEL: "Cancel the task; its branch is kept.",
}


@dataclass(frozen=True)
class PacketContext:
    """Everything `render_packet` needs to assemble one escalation packet (03 §8)."""

    task_id: str
    title: str
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


def _headline(context: PacketContext) -> str:
    step_label = context.step.value if context.step is not None else "no step"
    sentence = _HEADLINE_SENTENCES[context.reason]
    return f"{context.task_id} · {step_label} · {context.reason.value}: {sentence}"


def _options_section(options: tuple[EscalationOption, ...]) -> str:
    lines: list[str] = []
    for option in options:
        marker = " ★" if option.recommended else ""
        lines.append(f"- `{option.label.value}`{marker}: {_CONSEQUENCES[option.label]}")
    return "\n".join(lines)


def render_packet(context: PacketContext) -> str:
    """Render `context` as the packet markdown (03 §8).

    Headline, timeline, what was tried, evidence, the Leader's question and the resolvable
    options, exactly one of them recommended.
    """
    timeline = "\n".join(f"- {line}" for line in context.timeline) or "(no recorded events)"
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
        f"## Options\n\n{_options_section(context.options)}\n"
    )


class EscalationRaiser:
    """Builds the escalation packet, writes it, and records the escalation (03 §8)."""

    def __init__(
        self,
        escalations: EscalationStore,
        events: EventLog,
        runs: RunStore,
        project_root: Path,
    ) -> None:
        """Persist through `escalations`/`events`, and read session evidence from `runs`."""
        self._escalations = escalations
        self._events = events
        self._runs = runs
        self._project_root = project_root

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
    ) -> Escalation:
        """Build the packet for `task`, write it, and open its `Escalation` row (03 §8)."""
        options = options_for(reason)
        context = PacketContext(
            task_id=task.id,
            title=task.title,
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
        )
        packet = render_packet(context)
        headline = _headline(context)
        escalation_id = self._escalations.reserve_id()
        packet_path = self._packet_path(task.id, escalation_id)
        try:
            packet_path.parent.mkdir(parents=True, exist_ok=True)
            packet_path.write_text(packet)
            escalation = self._escalations.open_reserved(
                escalation_id,
                task.id,
                step=step,
                reason=reason,
                headline=headline,
                packet_path=str(packet_path),
                options=options,
            )
        except Exception:
            if packet_path.parent.is_dir() and packet_path.exists():
                packet_path.unlink()
            raise
        self._events.append(
            EventKind.ESCALATION,
            task_id=task.id,
            message=headline,
            data={"escalation_id": escalation.id, "reason": reason.value},
        )
        return escalation.model_copy(update={"packet_path": str(packet_path)})

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

    def __init__(self, tasks: TaskStore, raiser: EscalationRaiser, titles: TaskTitles) -> None:
        """Use the task store, title updater and packet raiser for dispatch failures."""
        self._tasks = tasks
        self._raiser = raiser
        self._titles = titles

    async def __call__(self, task: Task, error: Exception) -> None:
        """Mark `task` escalated and record the dispatch error for the operator."""
        transition = Transition.escalate_current(task, EscalationReason.ENGINE_ERROR)
        self._tasks.apply_transition(task.id, transition)
        self._tasks.set_wait_reason(task.id, None)
        current = self._tasks.get(task.id)
        if current is None:
            message = f"{task.id} vanished before dispatch failure escalation"
            raise ShikiruError(message)
        await self._titles.refresh(current)
        await self._raiser.raise_escalation(
            current,
            step=current.step,
            reason=EscalationReason.ENGINE_ERROR,
            question=str(error),
            attempted="Workspace or pane setup failed after scheduler retries.",
            reports=(),
            facts="",
            view_tail="",
        )
