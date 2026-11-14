"""Token category rules: unknown stays unknown, and processed sums need every category."""

from __future__ import annotations

import pytest

from shikiru.agents.contracts import Usage
from shikiru.domain.token_usage import (
    CategoryTotal,
    TokenTotals,
    TotalStatus,
    processed_sum,
    processed_tokens,
    sum_usages,
)


def _usage(
    uncached: int | None, output: int | None, cache_read: int | None, cache_write: int | None
) -> Usage:
    return Usage(uncached, output, cache_read, cache_write, None, "unknown", None)


@pytest.mark.parametrize(
    ("categories", "expected"),
    [
        ((100, 20, 30, 40), 190),
        ((100, None, 30, 40), None),
        ((None, None, None, None), None),
        ((0, 0, 0, 0), 0),
    ],
)
def test_processed_sum_is_known_only_when_every_category_is_reported(
    categories: tuple[int | None, ...], expected: int | None
) -> None:
    assert processed_sum(categories) == expected


def test_processed_tokens_of_a_usage_follows_the_same_rule() -> None:
    assert processed_tokens(_usage(100, 20, 30, 40)) == 190
    assert processed_tokens(_usage(100, 20, 30, None)) is None


@pytest.mark.parametrize(
    ("values", "total", "status"),
    [
        ((10, 20), 30, TotalStatus.COMPLETE),
        ((10, None), 10, TotalStatus.PARTIAL),
        ((None, None), None, TotalStatus.UNREPORTED),
        ((), None, TotalStatus.UNREPORTED),
    ],
)
def test_category_total_sums_reported_runs_and_says_how_complete_it_is(
    values: tuple[int | None, ...], total: int | None, status: TotalStatus
) -> None:
    category = CategoryTotal()
    for value in values:
        category = category.with_value(value)

    assert category.total == total
    assert category.status is status


def test_token_totals_count_processed_only_for_runs_that_report_every_category() -> None:
    totals = sum_usages(
        (
            _usage(100, 20, 30, 40),
            _usage(1, 2, 3, None),
        )
    )

    assert totals.uncached_input.total == 101
    assert totals.cache_write.total == 40
    assert totals.cache_write.status is TotalStatus.PARTIAL
    assert totals.processed.total == 190
    assert totals.processed.status is TotalStatus.PARTIAL


def test_token_totals_of_no_runs_are_unreported_not_zero() -> None:
    totals = TokenTotals()

    assert totals.uncached_input.total is None
    assert totals.processed.total is None
    assert totals.processed.status is TotalStatus.UNREPORTED


def test_a_run_that_reported_no_tokens_keeps_every_total_partial() -> None:
    totals = sum_usages((_usage(100, 20, 30, 40), _usage(None, None, None, None)))

    assert totals.uncached_input.total == 100
    assert totals.uncached_input.status is TotalStatus.PARTIAL
    assert totals.processed.total == 190
    assert totals.processed.status is TotalStatus.PARTIAL


def test_wholly_unreported_runs_alone_leave_every_total_unreported() -> None:
    totals = sum_usages((_usage(None, None, None, None), _usage(None, None, None, None)))

    assert totals.output.total is None
    assert totals.output.status is TotalStatus.UNREPORTED
    assert totals.processed.status is TotalStatus.UNREPORTED


def test_a_run_missing_one_category_leaves_processed_unknown_for_that_run_only() -> None:
    totals = sum_usages((_usage(None, 5, 6, 7), _usage(1, 2, 3, 4)))

    assert totals.uncached_input.total == 1
    assert totals.uncached_input.status is TotalStatus.PARTIAL
    assert totals.processed.total == 10
    assert totals.processed.status is TotalStatus.PARTIAL
