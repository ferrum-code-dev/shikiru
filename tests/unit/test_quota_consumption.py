"""Quota consumption rules preserve only observed task intervals."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from shikiru.agents.contracts import QuotaBoundary, QuotaObservationTimeSource
from shikiru.domain.quota_consumption import (
    Coverage,
    CoverageGap,
    QuotaSample,
    QuotaTurn,
    TaskQuotaEvidence,
    TurnOutcome,
    WindowConsumption,
    task_quota_consumption,
)

_START = datetime(2026, 1, 1, tzinfo=UTC)
_RESET = _START + timedelta(hours=5)
_AGENT = "codex"
_WINDOW = "5h"
_TASK = "T-1"


def _turn(
    turn_id: int,
    *,
    started: int = 0,
    ended: int | None = 10,
    outcome: TurnOutcome = TurnOutcome.ENDED,
    task_id: str = _TASK,
    agent: str = _AGENT,
) -> QuotaTurn:
    return QuotaTurn(
        turn_id,
        task_id,
        agent,
        _START + timedelta(seconds=started),
        _START + timedelta(seconds=ended) if ended is not None else None,
        outcome,
    )


def _sample(
    turn_id: int,
    used: float,
    second: int,
    *,
    reset: datetime | None = _RESET,
    boundary: QuotaBoundary | None = None,
    time_source: QuotaObservationTimeSource = QuotaObservationTimeSource.COLLECTED,
    agent: str = _AGENT,
    window: str = _WINDOW,
) -> QuotaSample:
    return QuotaSample(
        turn_id,
        agent,
        window,
        used,
        reset,
        _START + timedelta(seconds=second),
        boundary,
        time_source,
    )


def _consumption(
    turns: tuple[QuotaTurn, ...],
    samples: tuple[QuotaSample, ...],
    other_turns: tuple[QuotaTurn, ...] = (),
) -> WindowConsumption:
    evidence = TaskQuotaEvidence(_TASK, turns, samples, other_turns)
    [result] = task_quota_consumption(evidence)
    return result


@pytest.mark.parametrize(
    ("samples", "consumed", "coverage", "gaps"),
    [
        pytest.param(
            (
                _sample(1, 20, 1, boundary=QuotaBoundary.START),
                _sample(1, 23.5, 2, boundary=QuotaBoundary.END),
            ),
            3.5,
            Coverage.COMPLETE,
            (),
            id="same-reset-period",
        ),
        pytest.param(
            (
                _sample(1, 20, 1, boundary=QuotaBoundary.START),
                _sample(1, 20, 2, boundary=QuotaBoundary.END),
            ),
            0.0,
            Coverage.COMPLETE,
            (),
            id="observed-zero-is-a-value",
        ),
        pytest.param(
            (_sample(1, 20, 1), _sample(1, 23.5, 2)),
            3.5,
            Coverage.PARTIAL,
            (CoverageGap.NO_END_BOUNDARY, CoverageGap.NO_START_BOUNDARY),
            id="in-turn-pair-is-partial-without-boundaries",
        ),
    ],
)
def test_ordered_observations_charge_only_their_percentage_point_increase(
    samples: tuple[QuotaSample, ...],
    consumed: float,
    coverage: Coverage,
    gaps: tuple[CoverageGap, ...],
) -> None:
    result = _consumption((_turn(1),), samples)

    assert result.consumed_pct == pytest.approx(consumed)
    assert result.coverage is coverage
    assert result.gaps == gaps


@pytest.mark.parametrize(
    ("samples", "consumed", "coverage", "gap"),
    [
        pytest.param(
            (
                _sample(1, 20, 1, boundary=QuotaBoundary.START, reset=_RESET),
                _sample(1, 80, 2, reset=_RESET + timedelta(hours=5)),
                _sample(1, 82, 3, boundary=QuotaBoundary.END, reset=_RESET + timedelta(hours=5)),
            ),
            2.0,
            Coverage.PARTIAL,
            CoverageGap.RESET_CHANGED,
            id="new-reset-needs-a-new-baseline",
        ),
        pytest.param(
            (
                _sample(1, 50, 1, boundary=QuotaBoundary.START),
                _sample(1, 8, 2),
                _sample(1, 9, 3, boundary=QuotaBoundary.END),
            ),
            1.0,
            Coverage.PARTIAL,
            CoverageGap.USAGE_DECREASED,
            id="decrease-needs-a-new-baseline",
        ),
        pytest.param(
            (
                _sample(1, 20, 1, boundary=QuotaBoundary.START, reset=None),
                _sample(1, 23, 2, boundary=QuotaBoundary.END, reset=None),
            ),
            None,
            Coverage.UNAVAILABLE,
            CoverageGap.MISSING_RESET,
            id="missing-reset-identity-is-not-chargeable",
        ),
    ],
)
def test_reset_changes_and_usage_decreases_split_accounting(
    samples: tuple[QuotaSample, ...],
    consumed: float | None,
    coverage: Coverage,
    gap: CoverageGap,
) -> None:
    result = _consumption((_turn(1),), samples)

    assert result.consumed_pct == consumed
    assert result.coverage is coverage
    assert gap in result.gaps


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            (
                (_turn(1),),
                (),
                TurnOutcome.ENDED,
                None,
                Coverage.UNAVAILABLE,
                (CoverageGap.NO_OBSERVATIONS,),
            ),
            id="no-observation",
        ),
        pytest.param(
            (
                (_turn(1),),
                (_sample(1, 23, 2, boundary=QuotaBoundary.END),),
                TurnOutcome.ENDED,
                None,
                Coverage.UNAVAILABLE,
                (CoverageGap.NO_START_BOUNDARY,),
            ),
            id="lone-end-cannot-establish-total",
        ),
        pytest.param(
            (
                (_turn(1),),
                (_sample(1, 20, 1), _sample(1, 23, 2)),
                TurnOutcome.STALLED,
                3.0,
                Coverage.PARTIAL,
                (
                    CoverageGap.INTERRUPTED,
                    CoverageGap.NO_END_BOUNDARY,
                    CoverageGap.NO_START_BOUNDARY,
                ),
            ),
            id="interrupted-turn-retains-its-observed-delta",
        ),
    ],
)
def test_coverage_distinguishes_missing_data_from_partial_and_interrupted_work(
    case: tuple[
        tuple[QuotaTurn, ...],
        tuple[QuotaSample, ...],
        TurnOutcome,
        float | None,
        Coverage,
        tuple[CoverageGap, ...],
    ],
) -> None:
    turns, samples, outcome, expected, coverage, gaps = case
    result = _consumption((replace(turns[0], outcome=outcome),), samples)

    assert result.consumed_pct == expected
    assert result.coverage is coverage
    assert result.gaps == gaps


def test_out_of_order_observation_is_ignored_and_marks_coverage_partial() -> None:
    result = _consumption(
        (_turn(1),),
        (
            _sample(1, 20, 1),
            _sample(1, 23, 3),
            _sample(1, 21, 2),
            _sample(1, 24, 4, boundary=QuotaBoundary.END),
        ),
    )

    assert result.consumed_pct == pytest.approx(4)
    assert result.coverage is Coverage.PARTIAL
    assert CoverageGap.STALE_OBSERVATION in result.gaps


def test_cached_pre_turn_sample_cannot_establish_a_baseline() -> None:
    result = _consumption(
        (_turn(1, started=2, ended=5),),
        (
            _sample(1, 20, 1),
            _sample(1, 23, 4),
        ),
    )

    assert result.consumed_pct is None
    assert result.coverage is Coverage.UNAVAILABLE
    assert CoverageGap.STALE_OBSERVATION in result.gaps


def test_unknown_time_snapshots_are_preserved_as_unchargeable_evidence() -> None:
    result = _consumption(
        (_turn(1),),
        (
            _sample(1, 20, 1, time_source=QuotaObservationTimeSource.UNKNOWN),
            _sample(1, 23.5, 2, time_source=QuotaObservationTimeSource.UNKNOWN),
        ),
    )

    assert result.consumed_pct is None
    assert result.coverage is Coverage.UNAVAILABLE
    assert CoverageGap.UNKNOWN_OBSERVATION_TIME in result.gaps


def test_legacy_turn_history_keeps_later_observed_usage_partial() -> None:
    result = _consumption(
        (
            _turn(1, started=0, ended=5, outcome=TurnOutcome.LEGACY),
            _turn(2, started=6, ended=10),
        ),
        (
            _sample(2, 20, 7, boundary=QuotaBoundary.START),
            _sample(2, 23.5, 8, boundary=QuotaBoundary.END),
        ),
    )

    assert result.consumed_pct == pytest.approx(3.5)
    assert result.coverage is Coverage.PARTIAL
    assert CoverageGap.LEGACY_HISTORY in result.gaps


@pytest.mark.parametrize(
    ("other_start", "other_end", "expected", "coverage", "gap"),
    [
        pytest.param(2, 3, None, Coverage.UNAVAILABLE, CoverageGap.SHARED_ACCOUNT, id="overlap"),
        pytest.param(4, 6, 3.0, Coverage.COMPLETE, None, id="after-observed-interval"),
    ],
)
def test_overlapping_provider_turns_are_not_allocated_as_exact_task_usage(
    other_start: int,
    other_end: int,
    expected: float | None,
    coverage: Coverage,
    gap: CoverageGap | None,
) -> None:
    result = _consumption(
        (_turn(1),),
        (
            _sample(1, 20, 1, boundary=QuotaBoundary.START),
            _sample(1, 23, 4, boundary=QuotaBoundary.END),
        ),
        (_turn(2, started=other_start, ended=other_end, task_id="T-2"),),
    )

    assert result.consumed_pct == expected
    assert result.coverage is coverage
    if gap is not None:
        assert gap in result.gaps


def test_aggregation_sums_disjoint_turns_and_ignores_duplicate_samples() -> None:
    result = _consumption(
        (_turn(1, started=0, ended=3), _turn(2, started=4, ended=7)),
        (
            _sample(1, 20, 1, boundary=QuotaBoundary.START),
            _sample(1, 23.5, 2, boundary=QuotaBoundary.END),
            _sample(1, 23.5, 2, boundary=QuotaBoundary.END),
            _sample(2, 30, 5, boundary=QuotaBoundary.START),
            _sample(2, 32, 6, boundary=QuotaBoundary.END),
        ),
    )

    assert result.consumed_pct == pytest.approx(5.5)
    assert result.coverage is Coverage.COMPLETE


def test_aggregation_does_not_clamp_sum_across_reset_periods() -> None:
    second_reset = _RESET + timedelta(hours=5)
    third_reset = _RESET + timedelta(hours=10)
    result = _consumption(
        (
            _turn(1, started=0, ended=3),
            _turn(2, started=4, ended=7),
            _turn(3, started=8, ended=11),
        ),
        (
            _sample(1, 0, 1, boundary=QuotaBoundary.START),
            _sample(1, 100, 2, boundary=QuotaBoundary.END),
            _sample(2, 0, 5, boundary=QuotaBoundary.START, reset=second_reset),
            _sample(2, 100, 6, boundary=QuotaBoundary.END, reset=second_reset),
            _sample(3, 0, 9, boundary=QuotaBoundary.START, reset=third_reset),
            _sample(3, 100, 10, boundary=QuotaBoundary.END, reset=third_reset),
        ),
    )

    assert result.consumed_pct == pytest.approx(300)
    assert result.consumed_pct is not None
    assert result.consumed_pct > 100
    assert result.coverage is Coverage.COMPLETE
