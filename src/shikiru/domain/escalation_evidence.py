"""Read structured decision details from Shikiru's escalation packet format."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from shikiru.domain.commands import ResolutionKind
from shikiru.domain.reports import EscalationRequest

_QUESTION = "Question"
_AGENT_ALTERNATIVES = "Agent alternatives"
_RESOLUTIONS = "Options"
_TIMELINE = "Timeline"
_WHAT_WAS_TRIED = "What was tried"
_EVIDENCE = "Evidence"
_REPORTS = "Reports"
_FACTS = "Facts"
_RECENT_SIGNALS = "Recent turn signals"
_SESSIONS = "Sessions"
_NONE_RECORDED = "(none recorded)"
_NO_AGENT_PROPOSAL = (
    "No agent proposal was recorded. Engine recovery actions are listed separately."
)
_TOP_LEVEL_PREFIX = "## "
_SUBSECTION_PREFIX = "### "
_OPTION_PREFIX = "- `"
_OPTION_KIND_DELIMITER = "`"
_RECOMMENDED_MARKER = " ★"
_CONSEQUENCE_PREFIX = ": "
_EMPTY_PACKET = "escalation packet is empty"
_SECTION_MISSING = "escalation packet has no {heading!r} section"
_SECTION_AMBIGUOUS = "escalation packet has ambiguous {heading!r} section boundaries"
_SECTION_EMPTY = "escalation packet has an empty {heading!r} section"
_ALTERNATIVES_INVALID = "escalation packet has malformed 'Agent alternatives' data"
_OPTIONS_INVALID = "escalation packet has malformed 'Options' entry"
_OPTIONS_EMPTY = "escalation packet has no options"


@dataclass(frozen=True)
class PacketResolution:
    """One engine-allowed resolution and its recorded consequences."""

    kind: ResolutionKind
    consequences: str


@dataclass(frozen=True)
class EscalationPacket:
    """Structured information recognized in the built-in escalation packet format."""

    question: str | None
    agent_request: EscalationRequest | None
    resolutions: tuple[PacketResolution, ...]
    evidence_sections: tuple[tuple[str, str], ...]
    errors: tuple[str, ...]


def parse_escalation_packet(packet: str) -> EscalationPacket:
    """Read the decision and evidence sections used by the built-in packet renderer."""
    if not packet:
        return EscalationPacket(None, None, (), (), (_EMPTY_PACKET,))

    errors: list[str] = []
    question_end = (
        _AGENT_ALTERNATIVES if _section_exists(packet, _AGENT_ALTERNATIVES) else _RESOLUTIONS
    )
    question = _section(packet, _QUESTION, question_end, errors)
    if question is None:
        errors.append(_SECTION_MISSING.format(heading=_QUESTION))
    elif not question:
        errors.append(_SECTION_EMPTY.format(heading=_QUESTION))
        question = None

    agent_request = _agent_request(packet, errors)
    resolutions = _resolutions(packet, errors)
    evidence_sections = tuple(
        (label, content)
        for heading, following, label, subsection, following_subsection in (
            (_TIMELINE, _WHAT_WAS_TRIED, _TIMELINE, False, False),
            (_WHAT_WAS_TRIED, _EVIDENCE, _WHAT_WAS_TRIED, False, False),
            (_REPORTS, _FACTS, _REPORTS, True, True),
            (_FACTS, _RECENT_SIGNALS, _FACTS, True, True),
            (_RECENT_SIGNALS, _SESSIONS, _RECENT_SIGNALS, True, True),
            (_SESSIONS, _QUESTION, _SESSIONS, True, False),
        )
        if (
            content := _section(
                packet,
                heading,
                following,
                errors,
                subsection=subsection,
                following_subsection=following_subsection,
            )
        )
        is not None
    )
    return EscalationPacket(
        question,
        agent_request,
        resolutions,
        evidence_sections,
        tuple(errors),
    )


def _agent_request(packet: str, errors: list[str]) -> EscalationRequest | None:
    if not _section_exists(packet, _AGENT_ALTERNATIVES):
        return None
    section = _section(packet, _AGENT_ALTERNATIVES, _RESOLUTIONS, errors)
    if section in {_NONE_RECORDED, _NO_AGENT_PROPOSAL}:
        return None
    if section is None:
        return None
    try:
        return EscalationRequest.model_validate_json(section)
    except ValidationError:
        errors.append(_ALTERNATIVES_INVALID)
        return None


def _resolutions(packet: str, errors: list[str]) -> tuple[PacketResolution, ...]:
    section = _section(packet, _RESOLUTIONS, None, errors)
    if section is None:
        return ()
    resolutions: list[PacketResolution] = []
    for line in section.splitlines():
        if not line.startswith(_OPTION_PREFIX):
            continue
        entry = line.removeprefix(_OPTION_PREFIX)
        kind_text, separator, details = entry.partition(_OPTION_KIND_DELIMITER)
        if not separator:
            errors.append(_OPTIONS_INVALID)
            continue
        details = details.removeprefix(_RECOMMENDED_MARKER)
        if not details.startswith(_CONSEQUENCE_PREFIX):
            errors.append(_OPTIONS_INVALID)
            continue
        try:
            kind = ResolutionKind(kind_text)
        except ValueError:
            errors.append(_OPTIONS_INVALID)
            continue
        resolutions.append(PacketResolution(kind, details.removeprefix(_CONSEQUENCE_PREFIX)))
    if not resolutions:
        errors.append(_OPTIONS_EMPTY)
    return tuple(resolutions)


def _section_exists(packet: str, heading: str) -> bool:
    return f"{_TOP_LEVEL_PREFIX}{heading}" in packet.splitlines()


def _section(
    packet: str,
    heading: str,
    following: str | None,
    errors: list[str],
    *,
    subsection: bool = False,
    following_subsection: bool | None = None,
) -> str | None:
    lines = packet.splitlines()
    prefix = _SUBSECTION_PREFIX if subsection else _TOP_LEVEL_PREFIX
    marker = f"{prefix}{heading}"
    matches = [index for index, line in enumerate(lines) if line == marker]
    if not matches:
        errors.append(_SECTION_MISSING.format(heading=heading))
        return None
    if len(matches) != 1:
        errors.append(_SECTION_AMBIGUOUS.format(heading=heading))
        return None
    start = matches[0] + 1
    if following is None:
        end = len(lines)
    else:
        end_prefix = _SUBSECTION_PREFIX if following_subsection is True else _TOP_LEVEL_PREFIX
        if following_subsection is None:
            end_prefix = prefix
        following_marker = f"{end_prefix}{following}"
        following_matches = [index for index, line in enumerate(lines) if line == following_marker]
        if len(following_matches) != 1 or following_matches[0] < start:
            errors.append(_SECTION_AMBIGUOUS.format(heading=heading))
            return None
        end = following_matches[0]
    return "\n".join(lines[start:end]).strip()
