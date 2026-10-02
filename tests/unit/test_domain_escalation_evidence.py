from __future__ import annotations

from shikiru.domain.commands import ResolutionKind
from shikiru.domain.escalation_evidence import parse_escalation_packet
from shikiru.domain.reports import REPORT_JSON_INDENT, EscalationRequest, Option


def test_shikiru_packet_keeps_agent_alternatives_separate_from_engine_resolutions() -> None:
    agent_request = EscalationRequest(
        question="Which behavior should remain?",
        options=[
            Option(label="Keep behavior", consequences="Preserve callers.", recommended=True),
            Option(label="Change behavior", consequences="Update callers."),
        ],
    )
    packet = (
        "# T-1 · consult\n\n"
        "## Timeline\n\n- turn completed\n\n"
        "## What was tried\n\none attempt\n\n"
        "## Evidence\n\n"
        "### Reports\n\nreport\n\n"
        "### Facts\n\nfacts\n\n"
        "### Recent turn signals\n\nsignals\n\n"
        "### Sessions\n\nsessions\n\n"
        "## Question\n\nWhich behavior should remain?\n\n"
        "## Agent alternatives\n\n"
        f"{agent_request.model_dump_json(indent=REPORT_JSON_INDENT)}\n\n"
        "## Options\n\n"
        "- `answer` ★: Give the Leader guidance.\n"
        "- `accept`: Integrate the work.\n"
        "- `cancel`: Keep the branch.\n"
    )

    parsed = parse_escalation_packet(packet)

    assert parsed.question == agent_request.question
    assert parsed.agent_request == agent_request
    assert [option.kind for option in parsed.resolutions] == [
        ResolutionKind.ANSWER,
        ResolutionKind.ACCEPT,
        ResolutionKind.CANCEL,
    ]
    assert parsed.errors == ()
