"""The escalation resolution panel model: who, why, ordered choices and the preselection."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum, auto
from typing import TYPE_CHECKING

from shikiru.domain.attempts import AttemptItem, parse_attempt_lines
from shikiru.domain.commands import ResolutionKind
from shikiru.domain.escalation import (
    ESCALATION_CAUSES,
    RESOLUTION_CONSEQUENCES,
    EscalationOption,
    escalation_reason_label,
)
from shikiru.domain.tasks import DomainModel, EscalationReason

if TYPE_CHECKING:
    from shikiru.domain.escalation import Escalation
    from shikiru.domain.escalation_evidence import EscalationPacket

_WHAT_WAS_TRIED = "What was tried"
_ENGINE_ORDER = (ResolutionKind.ACCEPT, ResolutionKind.RETRY, ResolutionKind.CANCEL)


class ChoiceKind(StrEnum):
    """What sending a choice does."""

    ANSWER_OPTION = auto()
    OWN_ANSWER = auto()
    ACCEPT = auto()
    RETRY = auto()
    CANCEL = auto()


_CHOICE_BY_RESOLUTION = {
    ResolutionKind.ANSWER: ChoiceKind.OWN_ANSWER,
    ResolutionKind.ACCEPT: ChoiceKind.ACCEPT,
    ResolutionKind.RETRY: ChoiceKind.RETRY,
    ResolutionKind.CANCEL: ChoiceKind.CANCEL,
}


class PanelChoice(DomainModel):
    """One selectable row; `label` is set only for a Leader alternative."""

    kind: ChoiceKind
    consequence: str
    recommended: bool = False
    label: str | None = None
    needs_input: bool = False


class ResolutionPanel(DomainModel):
    """Everything the panel shows for one escalation; age is derived from `created_at`."""

    escalation_id: str
    task_id: str
    task_title: str
    reason: EscalationReason
    reason_label: str
    cause: str | None
    question: str | None
    created_at: datetime
    choices: tuple[PanelChoice, ...]
    preselected: int
    details: tuple[AttemptItem, ...]
    disabled_reason: str | None


def build_resolution_panel(
    escalation: Escalation,
    packet: EscalationPacket | None,
    task_title: str,
    disabled_reason: str | None,
) -> ResolutionPanel:
    """Build the panel for `escalation`; `packet` is `None` when it is missing or unreadable."""
    answerable = any(option.label is ResolutionKind.ANSWER for option in escalation.options)
    request = packet.agent_request if packet is not None and answerable else None
    choices = (
        *(
            PanelChoice(
                kind=ChoiceKind.ANSWER_OPTION,
                consequence=option.consequences,
                recommended=option.recommended,
                label=option.label,
                needs_input=option.needs_input,
            )
            for option in (request.options if request else ())
        ),
        *_engine_choices(escalation.options),
    )
    return ResolutionPanel(
        escalation_id=escalation.id,
        task_id=escalation.task_id,
        task_title=task_title,
        reason=escalation.reason,
        reason_label=escalation_reason_label(escalation.reason),
        cause=None
        if escalation.reason is EscalationReason.DECISION_REQUIRED
        else ESCALATION_CAUSES[escalation.reason],
        question=_question(packet, request.question if request else None) if answerable else None,
        created_at=escalation.created_at,
        choices=choices,
        preselected=_preselected(choices),
        details=_details(packet),
        disabled_reason=disabled_reason,
    )


def _engine_choices(options: tuple[EscalationOption, ...]) -> tuple[PanelChoice, ...]:
    by_kind = {option.label: option for option in options}
    ordered = (ResolutionKind.ANSWER, *_ENGINE_ORDER)
    return tuple(
        PanelChoice(
            kind=_CHOICE_BY_RESOLUTION[kind],
            consequence=RESOLUTION_CONSEQUENCES[kind],
            recommended=by_kind[kind].recommended,
        )
        for kind in ordered
        if kind in by_kind
    )


def _question(packet: EscalationPacket | None, request_question: str | None) -> str | None:
    return request_question or (packet.question if packet is not None else None)


def _preselected(choices: tuple[PanelChoice, ...]) -> int:
    for wanted in (ChoiceKind.ANSWER_OPTION, None):
        for index, choice in enumerate(choices):
            if choice.recommended and (wanted is None or choice.kind is wanted):
                return index
    return 0


def _details(packet: EscalationPacket | None) -> tuple[AttemptItem, ...]:
    if packet is None:
        return ()
    for label, content in packet.evidence_sections:
        if label == _WHAT_WAS_TRIED:
            return parse_attempt_lines(content)
    return ()
