"""Task quota consumption: the account-wide usage increase observed inside this task's turns.

Provider quota belongs to the account, so a task's share is an estimate: only the increase between
two ordered observations of the same reset period inside one of its turns is charged. Anything
that cannot be tied to such a pair stays visible as a coverage gap instead of invented usage.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from shikiru.agents.contracts import QuotaBoundary, QuotaObservationTimeSource

if TYPE_CHECKING:
    from collections.abc import Sequence


class TurnOutcome(StrEnum):
    """How a turn's quota collection ended."""

    RUNNING = "running"
    LEGACY = "legacy"
    ENDED = "ended"
    FAILED = "failed"
    STALLED = "stalled"
    PROTOCOL_ERROR = "protocol_error"
    ABORTED = "aborted"


INTERRUPTED_OUTCOMES = frozenset({TurnOutcome.STALLED, TurnOutcome.ABORTED})


class Coverage(StrEnum):
    """How completely a task's consumption of one window was observed."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class CoverageGap(StrEnum):
    """Why a window's consumption is partial or unavailable."""

    NO_OBSERVATIONS = "no_observations"
    NO_START_BOUNDARY = "no_start_boundary"
    NO_END_BOUNDARY = "no_end_boundary"
    STALE_OBSERVATION = "stale_observation"
    MISSING_RESET = "missing_reset"
    UNKNOWN_OBSERVATION_TIME = "unknown_observation_time"
    RESET_CHANGED = "reset_changed"
    USAGE_DECREASED = "usage_decreased"
    SHARED_ACCOUNT = "shared_account"
    INTERRUPTED = "interrupted"
    LEGACY_HISTORY = "legacy_history"


@dataclass(frozen=True)
class QuotaTurn:
    """One monitored turn attempt of a task on one provider."""

    id: int
    task_id: str
    agent: str
    started_at: datetime
    ended_at: datetime | None
    outcome: TurnOutcome


@dataclass(frozen=True)
class QuotaSample:
    """One quota observation recorded during a turn, in percent of its window."""

    turn_id: int
    agent: str
    window: str
    used_pct: float
    resets_at: datetime | None
    observed_at: datetime
    boundary: QuotaBoundary | None
    time_source: QuotaObservationTimeSource = QuotaObservationTimeSource.COLLECTED


@dataclass(frozen=True)
class TaskQuotaEvidence:
    """A task's turns and observations plus the other tasks' turns on the same providers."""

    task_id: str
    turns: Sequence[QuotaTurn]
    samples: Sequence[QuotaSample]
    other_turns: Sequence[QuotaTurn]


@dataclass(frozen=True)
class WindowConsumption:
    """Percentage points a task consumed of one provider window, with how well it was observed.

    `window` is `None` when a provider reported no window at all; `consumed_pct` is `None`
    when no usable pair of observations exists, which is different from an observed zero.
    """

    agent: str
    window: str | None
    consumed_pct: float | None
    coverage: Coverage
    gaps: tuple[CoverageGap, ...]


@dataclass(frozen=True)
class _Span:
    turn_id: int
    start: datetime
    end: datetime | None


_IntervalIdentity = tuple[datetime, float, datetime | None, datetime, float, datetime | None]


def task_quota_consumption(evidence: TaskQuotaEvidence) -> tuple[WindowConsumption, ...]:
    """Derive per provider/window consumption from `evidence`, ordered by provider and window."""
    agents = sorted({turn.agent for turn in evidence.turns})
    windows = {
        agent: sorted({sample.window for sample in evidence.samples if sample.agent == agent})
        for agent in agents
    }
    spans = _spans((*evidence.turns, *evidence.other_turns))
    results: list[WindowConsumption] = []
    for agent in agents:
        if not windows[agent]:
            gaps = {CoverageGap.NO_OBSERVATIONS} | _turn_gaps(evidence.turns, agent)
            results.append(_result(agent, None, [], gaps))
        results.extend(
            _window_consumption(evidence, agent, window, spans.get(agent, []))
            for window in windows[agent]
        )
    return tuple(results)


def _window_consumption(
    evidence: TaskQuotaEvidence, agent: str, window: str, spans: Sequence[_Span]
) -> WindowConsumption:
    samples = [
        sample for sample in evidence.samples if sample.agent == agent and sample.window == window
    ]
    deltas: list[float] = []
    gaps: set[CoverageGap] = set()
    seen_intervals: set[_IntervalIdentity] = set()
    for turn in sorted((t for t in evidence.turns if t.agent == agent), key=lambda t: t.id):
        turn_samples = [sample for sample in samples if sample.turn_id == turn.id]
        others = [span for span in spans if span.turn_id != turn.id]
        gaps |= _walk_turn(turn, turn_samples, others, deltas, seen_intervals)
    return _result(agent, window, deltas, gaps)


def _walk_turn(
    turn: QuotaTurn,
    samples: Sequence[QuotaSample],
    others: Sequence[_Span],
    deltas: list[float],
    seen_intervals: set[_IntervalIdentity],
) -> set[CoverageGap]:
    gaps: set[CoverageGap] = set()
    if turn.outcome is TurnOutcome.LEGACY:
        gaps.add(CoverageGap.LEGACY_HISTORY)
    if turn.outcome in INTERRUPTED_OUTCOMES:
        gaps.add(CoverageGap.INTERRUPTED)
    usable, sample_gaps = _ordered_samples(turn, samples)
    gaps |= sample_gaps
    if not usable:
        gaps.add(CoverageGap.NO_OBSERVATIONS)
        return gaps
    if usable[0].boundary is not QuotaBoundary.START:
        gaps.add(CoverageGap.NO_START_BOUNDARY)
    if usable[-1].boundary is not QuotaBoundary.END:
        gaps.add(CoverageGap.NO_END_BOUNDARY)
    baseline = usable[0]
    for sample in usable[1:]:
        gap = _interval_gap(baseline, sample, others)
        if gap is None:
            interval = _interval_identity(baseline, sample)
            if interval not in seen_intervals:
                seen_intervals.add(interval)
                deltas.append(sample.used_pct - baseline.used_pct)
        else:
            gaps.add(gap)
        baseline = sample
    return gaps


def _ordered_samples(
    turn: QuotaTurn, samples: Sequence[QuotaSample]
) -> tuple[list[QuotaSample], set[CoverageGap]]:
    gaps: set[CoverageGap] = set()
    usable: list[QuotaSample] = []
    seen_samples: set[tuple[datetime, float, datetime | None, QuotaBoundary | None]] = set()
    for sample in samples:
        identity = (sample.observed_at, sample.used_pct, sample.resets_at, sample.boundary)
        if sample.time_source is QuotaObservationTimeSource.UNKNOWN:
            gaps.add(CoverageGap.UNKNOWN_OBSERVATION_TIME)
            continue
        if _is_stale(sample, turn):
            gaps.add(CoverageGap.STALE_OBSERVATION)
            continue
        if usable and sample.observed_at < usable[-1].observed_at:
            gaps.add(CoverageGap.STALE_OBSERVATION)
            continue
        if identity in seen_samples:
            continue
        if usable and sample.observed_at == usable[-1].observed_at:
            gaps.add(CoverageGap.STALE_OBSERVATION)
            continue
        seen_samples.add(identity)
        usable.append(sample)
    return usable, gaps


def _interval_gap(
    baseline: QuotaSample, sample: QuotaSample, others: Sequence[_Span]
) -> CoverageGap | None:
    if baseline.resets_at is None or sample.resets_at is None:
        return CoverageGap.MISSING_RESET
    if baseline.resets_at != sample.resets_at:
        return CoverageGap.RESET_CHANGED
    if sample.used_pct < baseline.used_pct:
        return CoverageGap.USAGE_DECREASED
    if any(_overlaps(span, baseline.observed_at, sample.observed_at) for span in others):
        return CoverageGap.SHARED_ACCOUNT
    return None


def _is_stale(sample: QuotaSample, turn: QuotaTurn) -> bool:
    if sample.observed_at < turn.started_at and sample.boundary is not QuotaBoundary.START:
        return True
    return (
        turn.ended_at is not None
        and sample.observed_at > turn.ended_at
        and sample.boundary is not QuotaBoundary.END
    )


def _overlaps(span: _Span, start: datetime, end: datetime) -> bool:
    return span.start < end and (span.end is None or span.end > start)


def _interval_identity(baseline: QuotaSample, sample: QuotaSample) -> _IntervalIdentity:
    return (
        baseline.observed_at,
        baseline.used_pct,
        baseline.resets_at,
        sample.observed_at,
        sample.used_pct,
        sample.resets_at,
    )


def _turn_gaps(turns: Sequence[QuotaTurn], agent: str) -> set[CoverageGap]:
    gaps: set[CoverageGap] = set()
    for turn in turns:
        if turn.agent != agent:
            continue
        if turn.outcome in INTERRUPTED_OUTCOMES:
            gaps.add(CoverageGap.INTERRUPTED)
        if turn.outcome is TurnOutcome.LEGACY:
            gaps.add(CoverageGap.LEGACY_HISTORY)
    return gaps


def _spans(turns: Sequence[QuotaTurn]) -> dict[str, list[_Span]]:
    spans: dict[str, list[_Span]] = {}
    for turn in turns:
        if turn.outcome is TurnOutcome.LEGACY:
            continue
        spans.setdefault(turn.agent, []).append(_Span(turn.id, turn.started_at, turn.ended_at))
    return spans


def _result(
    agent: str, window: str | None, deltas: Sequence[float], gaps: set[CoverageGap]
) -> WindowConsumption:
    ordered = tuple(sorted(gaps))
    if not deltas:
        return WindowConsumption(agent, window, None, Coverage.UNAVAILABLE, ordered)
    coverage = Coverage.PARTIAL if gaps else Coverage.COMPLETE
    return WindowConsumption(agent, window, sum(deltas), coverage, ordered)
