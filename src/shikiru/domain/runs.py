"""Agent run model: one row per task role's session lineage."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum, auto

from shikiru.agents.contracts import (
    BILLING_MODE_UNKNOWN,
    BILLING_SOURCE_UNKNOWN,
    BillingMode,
    BillingSource,
    Usage,
)
from shikiru.domain.levels import displayed_pct
from shikiru.domain.tasks import DomainModel, Role
from shikiru.domain.token_usage import UsageAccounting

NO_USAGE = Usage(
    uncached_input_tokens=None,
    output_tokens=None,
    cache_read_tokens=None,
    cache_write_tokens=None,
    cost_usd=None,
    cost_basis="unknown",
    context_used_pct=None,
)

NO_TOKENS_USAGE = Usage(
    uncached_input_tokens=0,
    output_tokens=0,
    cache_read_tokens=0,
    cache_write_tokens=0,
    cost_usd=None,
    cost_basis="unknown",
    context_used_pct=None,
)


class RunStatus(StrEnum):
    """A run's lifecycle."""

    LIVE = auto()
    TAKEN_OVER = auto()
    CLOSED = auto()


class LegacyUsageSource(StrEnum):
    """Where a preserved ambiguous token observation came from."""

    PRE_CATEGORY_COLUMNS = auto()
    PREVIOUS_CATEGORY_FIELDS = auto()
    PREVIOUSLY_CLEARED_FIELDS = auto()


class LegacyUsage(DomainModel):
    """Historical token counters kept as evidence, not comparable usage."""

    source: LegacyUsageSource
    usage_reported: bool | None = None
    input_tokens: int | None = None
    uncached_input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None


class Run(DomainModel):
    """One agent session for a task role (mirrors the `runs` table)."""

    id: int
    task_id: str
    role: Role
    agent: str
    model: str
    effort: str | None = None
    session_id: str | None = None
    resume_command: str | None = None
    last_message: str | None = None
    status: RunStatus
    usage: Usage = NO_USAGE
    legacy_usage: LegacyUsage | None = None
    usage_reported: bool | None = None
    usage_accounting: UsageAccounting = UsageAccounting.CATEGORIES
    billing_mode: BillingMode = BILLING_MODE_UNKNOWN
    billing_source: BillingSource = BILLING_SOURCE_UNKNOWN
    started_at: datetime
    ended_at: datetime | None = None


def has_token_evidence(run: Run) -> bool:
    """Return whether `run` reports at least one token category."""
    usage = run.usage
    return any(
        category is not None
        for category in (
            usage.uncached_input_tokens,
            usage.output_tokens,
            usage.cache_read_tokens,
            usage.cache_write_tokens,
        )
    )


def is_legacy_usage(run: Run) -> bool:
    """Return whether the run's token observations use historical accounting."""
    return run.usage_accounting is UsageAccounting.LEGACY or (
        run.usage_reported is None and has_token_evidence(run)
    )


def normalized_usage(run: Run) -> Usage:
    """Return only token categories safe to combine with current provider accounting."""
    if not is_legacy_usage(run):
        return run.usage
    return Usage(
        uncached_input_tokens=None,
        output_tokens=None,
        cache_read_tokens=None,
        cache_write_tokens=None,
        cost_usd=run.usage.cost_usd,
        cost_basis=run.usage.cost_basis,
        context_used_pct=run.usage.context_used_pct,
    )


def starting_usage(run: Run) -> Usage:
    """Return the usage a new turn of `run` builds on.

    A run without a session has made no model calls, so its base is a known zero. A run with a
    session builds on what it recorded, which stays unknown where it reported nothing.
    """
    if run.session_id is None:
        return NO_TOKENS_USAGE
    return normalized_usage(run)


MISSING_TITLE_VALUE = "\N{EN DASH}"


def context_title(usage: Usage) -> str:
    """Format `usage`'s context share for a title (`42%`), or a dash if unreported."""
    if usage.context_used_pct is None:
        return MISSING_TITLE_VALUE
    return f"{displayed_pct(usage.context_used_pct)}%"


def cost_title(usage: Usage) -> str:
    """Format `usage`'s cost for a title (`$1.84`), or a dash if unreported."""
    if usage.cost_usd is None:
        return MISSING_TITLE_VALUE
    return f"${usage.cost_usd:.2f}"
