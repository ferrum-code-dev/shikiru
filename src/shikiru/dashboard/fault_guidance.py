"""Operator-facing summary and evidence of an open engine fault, from what was recorded."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from shikiru.dashboard.contract import EngineFaultView, FaultDetailRow
from shikiru.engine.pull_request_closing import CLOSE_FAULT_SCOPE
from shikiru.engine.push_service import PUSH_FAULT_SCOPE, REMOTE_CHECK_FAULT_SCOPE

if TYPE_CHECKING:
    from shikiru.domain.faults import FaultStreak

_RECONCILE_SCOPE_PREFIX = "reconcile "
_NOT_RECORDED = "not recorded"
_KEY_SEPARATOR = "\x00"
_CAUSE_LIMIT = 120
_ELLIPSIS = "\N{HORIZONTAL ELLIPSIS}"


class Recovery(StrEnum):
    """How Shikiru itself retries a fault: the distinctions the Operator plans around."""

    BACKOFF = "backoff"
    BOUNDED = "bounded"
    STARTUP_ONLY = "startup-only"
    UNKNOWN = "unknown"
    UNRECOVERABLE = "unrecoverable"


_STATUS = {
    Recovery.BACKOFF: "Retrying automatically",
    Recovery.BOUNDED: "Retrying a few times, then waiting for an engine restart",
    Recovery.STARTUP_ONLY: "Retried only at the next engine start",
    Recovery.UNKNOWN: "Retry timing unknown",
    Recovery.UNRECOVERABLE: "Not retried",
}
_UNRECOVERABLE_STEP = "Fix the code the traceback points to, then restart the engine"
_UNKNOWN_IMPACT = "not recorded for this kind of fault"
_UNKNOWN_STEP = "Read the cause in Details; no retry is known for this fault"
_RESTART_STEP = "Fix the cause, then restart the engine to retry sooner"


@dataclass(frozen=True)
class RetryPolicy:
    """The configured retry numbers the recovery descriptions quote."""

    backoff_initial_seconds: float
    backoff_max_seconds: float
    close_attempts: int


def _target(scope: str, prefix: str) -> str | None:
    head = f"{prefix} "
    return scope.removeprefix(head) if scope.startswith(head) else None


def recovery_of(scope: str, *, recoverable: bool) -> Recovery:
    """Return how `scope` is retried; a scope this view does not know retries at unknown times."""
    if not recoverable:
        return Recovery.UNRECOVERABLE
    if _target(scope, PUSH_FAULT_SCOPE) is not None or _target(scope, REMOTE_CHECK_FAULT_SCOPE):
        return Recovery.BACKOFF
    if scope == CLOSE_FAULT_SCOPE:
        return Recovery.BOUNDED
    if scope.startswith(_RECONCILE_SCOPE_PREFIX):
        return Recovery.STARTUP_ONLY
    return Recovery.UNKNOWN


def _impact_and_step(scope: str) -> tuple[str, str] | None:
    base = _target(scope, PUSH_FAULT_SCOPE)
    if base is not None:
        return (
            f"commits on {base} stay local",
            "Fix the cause git reports; Shikiru never forces a push",
        )
    if _target(scope, REMOTE_CHECK_FAULT_SCOPE) is not None:
        return (
            "no base is pushed until the remote can be read",
            "Fix the git remote configuration, for example a malformed .git/config",
        )
    if scope == CLOSE_FAULT_SCOPE:
        return "the pull request is not closed", _RESTART_STEP
    if scope.startswith(_RECONCILE_SCOPE_PREFIX):
        return "a startup recovery step did not complete", _RESTART_STEP
    return None


def _policy_text(recovery: Recovery, policy: RetryPolicy) -> str:
    backoff = f"{policy.backoff_initial_seconds:g}s doubling up to {policy.backoff_max_seconds:g}s"
    return {
        Recovery.BACKOFF: f"retried with backoff, {backoff}, while the engine runs",
        Recovery.BOUNDED: (
            f"up to {policy.close_attempts} attempts with backoff, {backoff}; "
            "then again only at the next engine start"
        ),
        Recovery.STARTUP_ONLY: "attempted again only at the next engine start",
        Recovery.UNKNOWN: "not recorded for this kind of fault",
        Recovery.UNRECOVERABLE: "none; a programming fault retrying cannot fix",
    }[recovery]


def _is_logged(scope: str) -> bool:
    return (
        _target(scope, PUSH_FAULT_SCOPE) is not None
        or _target(scope, REMOTE_CHECK_FAULT_SCOPE) is not None
        or scope.startswith(_RECONCILE_SCOPE_PREFIX)
    )


def _cause(fault: FaultStreak) -> str:
    if fault.detail is None:
        return f"{fault.error} (error text {_NOT_RECORDED})"
    lines = fault.detail.strip().splitlines()
    first = lines[0] if lines else fault.detail
    return first if len(first) <= _CAUSE_LIMIT else first[: _CAUSE_LIMIT - 1] + _ELLIPSIS


def _command_rows(fault: FaultStreak) -> tuple[FaultDetailRow, ...]:
    if fault.command is None:
        return (FaultDetailRow("Command", _NOT_RECORDED),)
    exit_code = _NOT_RECORDED if fault.exit_code is None else str(fault.exit_code)
    return (
        FaultDetailRow("Command", shlex.join(fault.command)),
        FaultDetailRow("Exit code", exit_code),
        FaultDetailRow("Stderr", _NOT_RECORDED if fault.stderr is None else fault.stderr.strip()),
    )


def _details(
    fault: FaultStreak,
    recovery: Recovery,
    policy: RetryPolicy,
    *,
    log_path: str,
    state_path: str,
) -> tuple[FaultDetailRow, ...]:
    logged = (
        (FaultDetailRow("Log", f"{log_path} (traceback of each failed attempt)"),)
        if _is_logged(fault.scope)
        else ()
    )
    return (
        FaultDetailRow("Error", fault.message if fault.detail is None else fault.detail.strip()),
        *_command_rows(fault),
        FaultDetailRow("First seen", fault.first_seen.isoformat()),
        FaultDetailRow("Latest", fault.last_seen.isoformat()),
        FaultDetailRow("Failures", str(fault.repeat)),
        FaultDetailRow("Retry policy", _policy_text(recovery, policy)),
        FaultDetailRow("Next retry", _NOT_RECORDED),
        FaultDetailRow("Traceback", _NOT_RECORDED if fault.traceback is None else fault.traceback),
        FaultDetailRow("Event", f"#{fault.event_id} in {state_path}"),
        *logged,
    )


def engine_fault_view(
    fault: FaultStreak, policy: RetryPolicy, *, log_path: str, state_path: str
) -> EngineFaultView:
    """Describe the open `fault` as a card summary plus the evidence recorded for it."""
    recovery = recovery_of(fault.scope, recoverable=fault.recoverable)
    known = _impact_and_step(fault.scope)
    impact, step = known if known is not None else (_UNKNOWN_IMPACT, _UNKNOWN_STEP)
    if recovery is Recovery.UNRECOVERABLE:
        step = _UNRECOVERABLE_STEP
    return EngineFaultView(
        key=f"{fault.task_id or ''}{_KEY_SEPARATOR}{fault.scope}",
        task_id=fault.task_id,
        scope=fault.scope,
        status=_STATUS[recovery],
        impact=impact,
        cause=_cause(fault),
        next_step=step,
        needs_action=recovery is not Recovery.BACKOFF,
        details=_details(fault, recovery, policy, log_path=log_path, state_path=state_path),
    )
