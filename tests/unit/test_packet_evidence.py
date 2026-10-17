from __future__ import annotations

import json

from shikiru.domain.packet_evidence import (
    DetailRow,
    Part,
    parse_reports,
    parse_signals,
)
from shikiru.domain.reports import (
    Commit,
    ConsultAnswer,
    EscalationRequest,
    ImplementationReport,
    Issue,
    Option,
    Report,
    Verdict,
)
from shikiru.domain.reports import (
    TestRun as RecordedTest,
)
from shikiru.domain.timeline_phrases import Tone

_RUNG = {"agent": "claude", "model": "opus", "effort": "high"}
_TURN = "002-implement-i1"
_TIME = "2026-01-01T10:00:05+00:00"
_OLD_SCHEMA = '{"verdict": "ok"}'


def _reports(*labelled: tuple[str, Report]) -> str:
    return "\n\n".join(f"**{label}**\n\n{report.model_dump_json()}" for label, report in labelled)


def _signal(signal: str, **fields: object) -> str:
    return json.dumps({"ts": _TIME, "turn": _TURN, "rung": _RUNG, "signal": signal, **fields})


def test_packet_reports_and_signals_read_as_toned_rows_and_structured_details() -> None:
    implementation = ImplementationReport(
        status="completed",
        summary="Added retries.",
        commits=[Commit(sha="a1b2c3d", subject="feat: retry"), Commit(sha="e4f5a6b", subject="t")],
        tests=[RecordedTest(name="test_cfg", result="failed", details="KeyError")],
        trackers_updated=True,
        risks=["timeout"],
    )
    verdict = Verdict(
        decision="changes_requested",
        summary="Fails.",
        issues=[Issue(severity="blocker", description="KeyError", location="cfg.py:4")],
    )
    consult = ConsultAnswer(
        decision="escalate",
        escalation=EscalationRequest(
            question="Which?", options=[Option(label="Keep", consequences="c", recommended=True)]
        ),
    )

    reports = parse_reports(
        _reports(
            ("002-implement-i1", implementation), ("003-verify-i1", verdict), ("004-x", consult)
        )
        + f"\n\n**005-implement-i2**\n\n{_OLD_SCHEMA}"
    )

    assert [(e.lead, e.kind, e.outcome) for e in reports] == [
        ("002", "implement", Part("completed · 2 commits · 1 failed", Tone.SUCCESS)),
        ("003", "verify", Part("changes requested (1 issue)", Tone.FAILURE)),
        ("004", "x", Part("escalate", Tone.WARNING)),
        ("005", "implement", Part(_OLD_SCHEMA)),
    ]
    assert reports[0].details == (
        DetailRow("Status", (Part("completed", Tone.SUCCESS),)),
        DetailRow("Summary", (Part("Added retries."),)),
        DetailRow("Commits", (Part("a1b2c3d", accent=True), Part(" feat: retry"))),
        DetailRow("", (Part("e4f5a6b", accent=True), Part(" t"))),
        DetailRow("Tests", (Part("test_cfg "), Part("failed", Tone.FAILURE), Part(" KeyError"))),
        DetailRow("Risks", (Part("timeout"),)),
    )
    assert reports[1].details[2] == DetailRow(
        "Issues",
        (Part("blocker", Tone.FAILURE), Part(" KeyError"), Part(" @ cfg.py:4")),
    )
    assert reports[2].details[-1] == DetailRow(
        "Escalation options", (Part("\N{BLACK STAR} Keep", accent=True), Part(" c"))
    )
    assert reports[3].details == (DetailRow("Report", (Part('{\n  "verdict": "ok"\n}'),)),)

    assert parse_reports("Implementation report evidence.")[0].details == (
        DetailRow("Text", (Part("Implementation report evidence."),)),
    )

    signals = parse_signals(
        "\n".join(
            (
                _signal("Message", text="Thinking\nabout it"),
                _signal("ToolStarted", name="Bash", detail='{"command": "pytest -x"}'),
                _signal("ToolFinished", name="Bash", outcome="failure", exit_code=1),
                _signal("UsageReported", usage={"input_tokens": 1}),
                _signal("TurnFailed", kind="quota", message="Rate limited", resets_at=None),
                '{"ts": "2026-01-01T10:01:00+00:00", "sig',
            )
        )
    )

    assert [(e.lead, e.kind, e.outcome) for e in signals] == [
        ("10:00:05", "message", Part('"Thinking about it"')),
        ("10:00:05", "tool", Part("ran pytest -x")),
        ("10:00:05", "tool done", Part("Bash · exit 1", Tone.FAILURE)),
        ("10:00:05", "usage", Part("")),
        ("10:00:05", "failed", Part("quota: Rate limited", Tone.FAILURE)),
        ("", "", Part('{"ts": "2026-01-01T10:01:00+00:00", "sig')),
    ]
    assert signals[1].details == (
        DetailRow("Signal", (Part("tool"),)),
        DetailRow("Rung", (Part("claude · opus · high"),)),
        DetailRow("Turn", (Part(_TURN),)),
        DetailRow("Tool", (Part("Bash"),)),
        DetailRow("Detail", (Part("pytest -x"),)),
    )
    assert signals[2].details[-1] == DetailRow("Result", (Part("failure · 1", Tone.FAILURE),))
    assert signals[0].details[-1] == DetailRow("Message", (Part("Thinking\nabout it"),))
    assert signals[5].details == (
        DetailRow("Line", (Part('{"ts": "2026-01-01T10:01:00+00:00", "sig'),)),
    )
