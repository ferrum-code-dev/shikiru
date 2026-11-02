"""Operator guidance for an open engine fault: how Shikiru recovers it and what to do."""

from __future__ import annotations

from shikiru.engine.pull_request_closing import CLOSE_FAULT_SCOPE
from shikiru.engine.push_service import PUSH_FAULT_SCOPE, REMOTE_CHECK_FAULT_SCOPE

_RECONCILE_SCOPE_PREFIX = "reconcile "
_BACKOFF_LINE = (
    "Recoverable: Shikiru retries it automatically with backoff and records the recovery "
    "once an attempt succeeds."
)
_BOUNDED_LINE = (
    "Recoverable: Shikiru retries it with backoff for a limited number of attempts, then waits "
    "for the next engine start. Operator action: fix the cause the evidence names, then restart "
    "the engine to retry it sooner."
)
_STARTUP_LINE = (
    "Recoverable: Shikiru does not retry it while the engine runs; it attempts this step again "
    "at the next engine start. Operator action: fix the cause the evidence names, then restart "
    "the engine."
)
_UNKNOWN_LINE = (
    "Recoverable: Shikiru records the recovery once the scope works again, but this view does "
    "not say when it is retried, so check the evidence before waiting on a retry."
)
_UNRECOVERABLE_LINE = (
    "Unrecoverable: a programming fault that retrying cannot fix. Operator action: fix the code "
    "the evidence points to, then restart the engine."
)
_REMOTE_CHECK_LINE = (
    "Shikiru could not read the repository's remote configuration, so no base is pushed until "
    "it can. Operator action: fix the git configuration named in the evidence, for example a "
    "malformed .git/config."
)
_PUSH_FORCE_LINE = (
    "Pushes never force. Operator action: fix the cause git names in the evidence, such as "
    "remote access or authentication, branch protection, or a {base} that diverged from the "
    "remote. Shikiru cannot resolve a divergence itself."
)
_PUSH_PENDING_LINE = (
    "Git could not push {base} to the remote, so its pending work stays unpushed; other bases "
    "keep pushing."
)
_LOGGED_EVIDENCE_LINE = (
    "Evidence: {log_path} holds the traceback of each failed attempt, and the recorded fault "
    "event in {state_path} holds its error message."
)
_EVENT_EVIDENCE_LINE = "Evidence: the recorded fault event in {state_path} holds its error message."


def _scope_target(scope: str, prefix: str) -> str | None:
    head = f"{prefix} "
    return scope.removeprefix(head) if scope.startswith(head) else None


def _is_backoff_scope(scope: str) -> bool:
    return any(
        _scope_target(scope, prefix) is not None
        for prefix in (PUSH_FAULT_SCOPE, REMOTE_CHECK_FAULT_SCOPE)
    )


def _is_logged_scope(scope: str) -> bool:
    return _is_backoff_scope(scope) or scope.startswith(_RECONCILE_SCOPE_PREFIX)


def _recovery_line(scope: str, *, recoverable: bool) -> str:
    if not recoverable:
        return _UNRECOVERABLE_LINE
    if _is_backoff_scope(scope):
        return _BACKOFF_LINE
    if scope == CLOSE_FAULT_SCOPE:
        return _BOUNDED_LINE
    if scope.startswith(_RECONCILE_SCOPE_PREFIX):
        return _STARTUP_LINE
    return _UNKNOWN_LINE


def _scope_lines(scope: str) -> tuple[str, ...]:
    base = _scope_target(scope, PUSH_FAULT_SCOPE)
    if base is not None:
        return (
            _PUSH_PENDING_LINE.format(base=base),
            _PUSH_FORCE_LINE.format(base=base),
        )
    if _scope_target(scope, REMOTE_CHECK_FAULT_SCOPE) is not None:
        return (_REMOTE_CHECK_LINE,)
    return ()


def engine_fault_guidance(
    scope: str, *, recoverable: bool, log_path: str, state_path: str
) -> tuple[str, ...]:
    """Return the lines explaining an open fault in `scope` and what to do about it.

    `log_path` is the engine log, which holds a traceback only for the scopes Shikiru logs;
    `state_path` is the state database, which holds the recorded fault event for every scope.
    """
    evidence = (_LOGGED_EVIDENCE_LINE if _is_logged_scope(scope) else _EVENT_EVIDENCE_LINE).format(
        log_path=log_path, state_path=state_path
    )
    return (
        _recovery_line(scope, recoverable=recoverable),
        *_scope_lines(scope),
        evidence,
    )
