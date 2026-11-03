"""Operator-facing summary and evidence of an open engine fault, from what was recorded."""

from __future__ import annotations

import re
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
_CAUSE_LIMIT = 160
_ELLIPSIS = "\N{HORIZONTAL ELLIPSIS}"


class Recovery(StrEnum):
    """How Shikiru itself retries a fault: the distinctions the Operator plans around."""

    BACKOFF = "backoff"
    BOUNDED = "bounded"
    STARTUP_ONLY = "startup-only"
    UNKNOWN = "unknown"


_STATUS = {
    Recovery.BACKOFF: "Retrying automatically",
    Recovery.BOUNDED: "Retrying a few times, then waiting for an engine restart",
    Recovery.STARTUP_ONLY: "Retried only at the next engine start",
    Recovery.UNKNOWN: "Retry timing unknown",
}
_UNEXPECTED_BACKOFF_STATUS = "Unexpected error · retrying automatically"
_UNRECOVERABLE_STEP = "Inspect the traceback and fix the unexpected error"
_UNKNOWN_CAUSE = "unknown: no error text was recorded"
_UNKNOWN_IMPACT = "not recorded for this kind of fault"
_UNKNOWN_STEP = "Read the cause in Details; no retry is known for this fault"
_RESTART_STEP = "Fix the cause, then restart the engine to retry sooner"
_LEGACY_COMMAND_FAILURE = re.compile(
    r"(?:CommandFailed: )?command .*?\) exited (-?\d+):\s*(.*)\Z", re.DOTALL
)


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
    """Return how `scope` is retried; a scope this view does not know retries at unknown times.

    Whether the error is `recoverable` (environmental) does not change who schedules the retry,
    except that the pull request close loop only retries environmental errors.
    """
    if _target(scope, PUSH_FAULT_SCOPE) is not None or _target(scope, REMOTE_CHECK_FAULT_SCOPE):
        return Recovery.BACKOFF
    if scope == CLOSE_FAULT_SCOPE:
        return Recovery.BOUNDED if recoverable else Recovery.UNKNOWN
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
        Recovery.BACKOFF: (
            f"background push service logs failures and retries with backoff ({backoff}) "
            "while the engine runs, even if the guard reaches its unexpected fault limit"
        ),
        Recovery.BOUNDED: (
            f"up to {policy.close_attempts} attempts with backoff, {backoff}; "
            "then again only at the next engine start"
        ),
        Recovery.STARTUP_ONLY: "attempted again only at the next engine start",
        Recovery.UNKNOWN: "not recorded for this kind of fault",
    }[recovery]


def _is_logged(scope: str) -> bool:
    return (
        _target(scope, PUSH_FAULT_SCOPE) is not None
        or _target(scope, REMOTE_CHECK_FAULT_SCOPE) is not None
        or scope.startswith(_RECONCILE_SCOPE_PREFIX)
    )


def _shorten(text: str) -> str:
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= _CAUSE_LIMIT else one_line[: _CAUSE_LIMIT - 1] + _ELLIPSIS


def _recorded_error(fault: FaultStreak) -> str:
    if fault.detail is not None:
        return fault.detail
    return fault.message.removeprefix(f"{fault.scope} failed: ")


def _legacy_command_cause(error: str) -> str | None:
    match = _LEGACY_COMMAND_FAILURE.fullmatch(error)
    if match is None:
        return None
    exit_code, stderr = match.groups()
    return f"exit {exit_code}: {stderr.strip()}" if stderr.strip() else f"exit {exit_code}"


def _cause(fault: FaultStreak) -> str:
    if fault.exit_code is not None:
        stderr = (fault.stderr or "").strip()
        return _shorten(
            f"exit {fault.exit_code}: {stderr}" if stderr else f"exit {fault.exit_code}"
        )
    error = _recorded_error(fault)
    return _shorten(_legacy_command_cause(error) or error) or _UNKNOWN_CAUSE


def _command_rows(fault: FaultStreak) -> tuple[FaultDetailRow, ...]:
    command = _NOT_RECORDED if fault.command is None else shlex.join(fault.command)
    exit_code = _NOT_RECORDED if fault.exit_code is None else str(fault.exit_code)
    return (
        FaultDetailRow("Command", command),
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
        FaultDetailRow("Error", _recorded_error(fault).strip() or _UNKNOWN_CAUSE),
        *_command_rows(fault),
        FaultDetailRow("First seen", fault.first_seen.isoformat()),
        FaultDetailRow("Latest", fault.last_seen.isoformat()),
        FaultDetailRow("Failures", str(fault.repeat)),
        FaultDetailRow(
            "Classification",
            "environmental failure" if fault.recoverable else "unexpected software error",
        ),
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
    if not fault.recoverable:
        step = _UNRECOVERABLE_STEP
    return EngineFaultView(
        key=f"{fault.task_id or ''}{_KEY_SEPARATOR}{fault.scope}",
        task_id=fault.task_id,
        scope=fault.scope,
        status=(
            _UNEXPECTED_BACKOFF_STATUS
            if recovery is Recovery.BACKOFF and not fault.recoverable
            else _STATUS[recovery]
        ),
        impact=impact,
        cause=_cause(fault),
        next_step=step,
        needs_action=recovery is not Recovery.BACKOFF or not fault.recoverable,
        details=_details(fault, recovery, policy, log_path=log_path, state_path=state_path),
    )
