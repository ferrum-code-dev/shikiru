from __future__ import annotations

from datetime import UTC, datetime

import pytest

from shikiru.domain.timeline import (
    TIMELINE_EVIDENCE_CATEGORY,
    TIMELINE_TOOL_CATEGORY,
    parse_signal_row,
)


@pytest.mark.parametrize(
    ("raw", "incomplete", "expected_category", "expected_timestamp", "expected_error"),
    [
        pytest.param(
            b'{"ts":"2026-01-02T03:04:05+02:00","turn":"01-implement-i1",'
            b'"signal":"ToolFinished","name":"pytest"}',
            False,
            TIMELINE_TOOL_CATEGORY,
            datetime(2026, 1, 2, 1, 4, 5, tzinfo=UTC),
            None,
            id="offset-timestamp-and-normalized-tool-signal",
        ),
        pytest.param(
            b'{"signal":"ToolStarted"}',
            False,
            TIMELINE_TOOL_CATEGORY,
            None,
            "recorded timestamp is missing",
            id="missing-timestamp-remains-evidence",
        ),
        pytest.param(
            b'{"ts":"2026-01-02T03:04:05","signal":"ToolStarted"}',
            False,
            TIMELINE_TOOL_CATEGORY,
            None,
            "recorded timestamp has no UTC offset",
            id="naive-timestamp-is-not-assumed-utc",
        ),
        pytest.param(
            b'{"ts":',
            True,
            TIMELINE_EVIDENCE_CATEGORY,
            None,
            "recorded timestamp is missing",
            id="partial-json-row-remains-visible",
        ),
    ],
)
def test_normalized_signal_parser_preserves_timestamps_and_partial_evidence(
    raw: bytes,
    incomplete: bool,
    expected_category: str,
    expected_timestamp: datetime | None,
    expected_error: str | None,
) -> None:
    parsed = parse_signal_row(
        raw,
        role="implementer",
        start_offset=7,
        end_offset=7 + len(raw),
        incomplete=incomplete,
    )

    assert parsed.role == "implementer"
    assert parsed.category == expected_category
    assert parsed.timestamp == expected_timestamp
    assert parsed.timestamp_error == expected_error
    assert parsed.incomplete is incomplete
