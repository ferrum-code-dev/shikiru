"""The evidence disclosure of a task's Blockers: quota readings, notes and raw events."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Group
from rich.table import Table

from shikiru.clock import to_local
from shikiru.domain.levels import displayed_pct
from shikiru.domain.quota_waits import (
    QUOTA_NOT_REPORTED,
    provider_reset_text,
    quota_window_label,
)
from shikiru.ui.age import AGE_NOW, format_short_age
from shikiru.ui.section import grey
from shikiru.ui.tokens import SECONDARY_TEXT_COLOR

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, timedelta

    from rich.console import RenderableType
    from rich.text import Text

    from shikiru.dashboard.contract import BlockerEvidenceView, QuotaEvidenceView, QuotaReadingView

_OBSERVED_PREFIX = "observed"
_AGO_SUFFIX = "ago"
_JUST_NOW = "just now"
_DIFFERENT_TIMES = "at different times"
_RANGE_SEPARATOR = " to "
_WINDOW_HEADER = "Window"
_USED_HEADER = "Used"
_RESET_HEADER = "Provider reset"
_NO_READING = "No provider quota reading is recorded."
_COLUMN_GAP = 2


def evidence_headline(evidence: BlockerEvidenceView, now: datetime) -> str | None:
    """Return how old the observations are: one age, or the span of differing ages."""
    ages = sorted(
        [now - reading.observed_at for quota in evidence.quotas for reading in quota.readings]
        + [now - observed for observed in evidence.observed_at]
    )
    if not ages:
        return None
    newest, oldest = ages[0], ages[-1]
    newest_age = _observed_age(newest)
    oldest_age = _observed_age(oldest)
    if newest == oldest:
        return f"{_OBSERVED_PREFIX} {newest_age}"
    if newest_age == oldest_age:
        return f"{_OBSERVED_PREFIX} {_DIFFERENT_TIMES} ({oldest_age})"
    return f"{_OBSERVED_PREFIX} {newest_age}{_RANGE_SEPARATOR}{oldest_age}"


def evidence_body(evidence: BlockerEvidenceView, now: datetime) -> RenderableType:
    """Return the normal evidence: each provider's readings and reset, then the notes.

    A provider label heads each group only when more than one provider is blocked.
    """
    labelled = len(evidence.quotas) > 1
    parts: list[RenderableType] = []
    for quota in evidence.quotas:
        if labelled:
            parts.append(grey(quota.agent))
        parts.append(_quota_block(quota, to_local(now)))
    parts.extend(grey(note) for note in evidence.notes)
    return Group(*parts)


def raw_evidence_text(evidence: BlockerEvidenceView) -> Text:
    """Return the raw events in grey: exact timestamps, event references and diagnostics."""
    return grey("\n".join(evidence.raw))


def _quota_block(quota: QuotaEvidenceView, now: datetime) -> RenderableType:
    parts: list[RenderableType] = (
        [_readings_table(quota.readings, now)] if quota.readings else [grey(_NO_READING)]
    )
    if quota.retry_note is not None:
        parts.append(grey(quota.retry_note))
    return Group(*parts)


def _readings_table(readings: Sequence[QuotaReadingView], now: datetime) -> Table:
    table = Table(
        box=None,
        show_edge=False,
        pad_edge=False,
        padding=(0, _COLUMN_GAP, 0, 0),
        header_style=SECONDARY_TEXT_COLOR,
        style=SECONDARY_TEXT_COLOR,
    )
    table.add_column(_WINDOW_HEADER)
    table.add_column(_USED_HEADER, justify="right")
    table.add_column(_RESET_HEADER)
    for reading in readings:
        table.add_row(
            quota_window_label(reading.window).capitalize(),
            f"{displayed_pct(reading.used_pct)}%",
            _reset_text(reading.resets_at, now),
        )
    return table


def _reset_text(resets_at: datetime | None, now: datetime) -> str:
    if resets_at is None:
        return QUOTA_NOT_REPORTED
    return provider_reset_text(to_local(resets_at), now)


def _observed_age(age: timedelta) -> str:
    short = format_short_age(age)
    return _JUST_NOW if short == AGE_NOW else f"{short} {_AGO_SUFFIX}"
