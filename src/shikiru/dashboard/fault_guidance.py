"""Operator guidance for an open engine fault: what Shikiru does next and what to do."""

from __future__ import annotations

from shikiru.engine.push_service import PUSH_FAULT_SCOPE, REMOTE_CHECK_FAULT_SCOPE

_RECOVERABLE_LINE = (
    "Recoverable: Shikiru retries it automatically with backoff and records the recovery "
    "once an attempt succeeds."
)
_UNRECOVERABLE_LINE = (
    "Unrecoverable: a programming fault that retrying cannot fix. Operator action: fix the "
    "code named in the traceback, then restart the engine."
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
_EVIDENCE_LINE = (
    "Evidence: {evidence} holds each failed attempt's traceback, including git's error output, "
    "and its retry delay."
)


def _push_lines(base: str) -> tuple[str, ...]:
    return (
        _PUSH_PENDING_LINE.format(base=base),
        _PUSH_FORCE_LINE.format(base=base),
    )


def _scope_target(scope: str, prefix: str) -> str | None:
    head = f"{prefix} "
    return scope.removeprefix(head) if scope.startswith(head) else None


def engine_fault_guidance(scope: str, *, recoverable: bool, evidence: str) -> tuple[str, ...]:
    """Return the lines explaining an open fault in `scope` and what to do about it.

    `evidence` is where the engine recorded each failed attempt.
    """
    base = _scope_target(scope, PUSH_FAULT_SCOPE)
    if base is not None:
        scope_lines = _push_lines(base)
    elif _scope_target(scope, REMOTE_CHECK_FAULT_SCOPE) is not None:
        scope_lines = (_REMOTE_CHECK_LINE,)
    else:
        scope_lines = ()
    return (
        _RECOVERABLE_LINE if recoverable else _UNRECOVERABLE_LINE,
        *scope_lines,
        _EVIDENCE_LINE.format(evidence=evidence),
    )
