"""Token usage categories and totals that keep unreported values unknown."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from shikiru.agents.contracts import Usage

PROCESSED_TOKENS_NOTE = (
    "Processed tokens sum uncached input, cache reads, cache writes and output over every model "
    "call. They are not context occupancy, cost, or subscription quota."
)
LEGACY_USAGE_NOTE = (
    "Historical counters may be partial or use prior accounting; they are retained as evidence "
    "and excluded from normalized token totals."
)


class UsageAccounting(StrEnum):
    """Which token accounting a run's usage values were recorded under."""

    CATEGORIES = auto()
    LEGACY = auto()


class TotalStatus(StrEnum):
    """Whether a total covers every run, some runs, or none of them."""

    COMPLETE = auto()
    PARTIAL = auto()
    UNREPORTED = auto()


@dataclass(frozen=True)
class CategoryTotal:
    """One token category summed over runs; a run that did not report it stays unknown."""

    reported_sum: int = 0
    reported_runs: int = 0
    unreported_runs: int = 0

    def with_value(self, value: int | None) -> CategoryTotal:
        """Return this total with one more run's value, or one more unreported run."""
        if value is None:
            return replace(self, unreported_runs=self.unreported_runs + 1)
        return replace(
            self,
            reported_sum=self.reported_sum + value,
            reported_runs=self.reported_runs + 1,
        )

    @property
    def total(self) -> int | None:
        """Return the sum of the reported runs, or `None` when no run reported it."""
        return self.reported_sum if self.reported_runs else None

    @property
    def status(self) -> TotalStatus:
        """Return whether the total covers every run, some of them, or none."""
        if self.reported_runs == 0:
            return TotalStatus.UNREPORTED
        if self.unreported_runs == 0:
            return TotalStatus.COMPLETE
        return TotalStatus.PARTIAL


def processed_sum(categories: Sequence[int | None]) -> int | None:
    """Return the sum of `categories`, or `None` unless every category is reported."""
    known = [category for category in categories if category is not None]
    return sum(known) if len(known) == len(categories) else None


def processed_tokens(usage: Usage) -> int | None:
    """Return the four categories summed, or `None` unless every category is reported."""
    return processed_sum(
        (
            usage.uncached_input_tokens,
            usage.cache_read_tokens,
            usage.cache_write_tokens,
            usage.output_tokens,
        )
    )


@dataclass(frozen=True)
class TokenTotals:
    """The four token categories and processed tokens, summed over a set of runs.

    Every run counts toward every total. A run that did not report a category leaves it
    unreported, so the total's status is partial rather than complete, and a run that
    reported nothing makes every total partial as well. `processed` counts a run only when
    it reports all four categories.
    """

    uncached_input: CategoryTotal = CategoryTotal()
    cache_read: CategoryTotal = CategoryTotal()
    cache_write: CategoryTotal = CategoryTotal()
    output: CategoryTotal = CategoryTotal()
    processed: CategoryTotal = CategoryTotal()

    def with_usage(self, usage: Usage) -> TokenTotals:
        """Return these totals with one run's usage added, unknown categories included."""
        return TokenTotals(
            uncached_input=self.uncached_input.with_value(usage.uncached_input_tokens),
            cache_read=self.cache_read.with_value(usage.cache_read_tokens),
            cache_write=self.cache_write.with_value(usage.cache_write_tokens),
            output=self.output.with_value(usage.output_tokens),
            processed=self.processed.with_value(processed_tokens(usage)),
        )


def sum_usages(usages: Iterable[Usage]) -> TokenTotals:
    """Return totals over `usages`; a category a run did not report stays unknown."""
    totals = TokenTotals()
    for usage in usages:
        totals = totals.with_usage(usage)
    return totals
