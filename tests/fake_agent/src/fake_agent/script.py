"""Parses `SHIKIRU_FAKE_SCRIPT` into `TurnStep`s (see the `fake_agent` package docstring)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast

STEP_ACCEPT = "accept"
STEP_CHANGES_REQUESTED = "changes_requested"
STEP_NEEDS_DECISION = "needs_decision"
STEP_NO_REPORT = "no_report"
STEP_FAIL = "fail"
STEP_CRASH = "crash"
STEP_STALL = "stall"
STEP_DIALOG = "dialog"

_KNOWN_STEPS = frozenset(
    {
        STEP_ACCEPT,
        STEP_CHANGES_REQUESTED,
        STEP_NEEDS_DECISION,
        STEP_NO_REPORT,
        STEP_FAIL,
        STEP_CRASH,
        STEP_STALL,
        STEP_DIALOG,
    }
)


@dataclass(frozen=True, slots=True)
class TurnStep:
    """One scripted turn behaviour, with whichever of its arguments apply to `kind`."""

    kind: str
    question: str = ""
    message: str = ""
    failure_kind: str = ""
    resets_at: str | None = None


def _step_from_json(value: Any) -> TurnStep:
    if isinstance(value, str):
        kind = value
        fields: dict[str, Any] = {}
    elif isinstance(value, dict):
        raw = cast("dict[str, Any]", value)
        kind = raw["kind"]
        fields = {key: item for key, item in raw.items() if key != "kind"}
    else:
        message = f"fake agent script step must be a string or object, got {value!r}"
        raise TypeError(message)
    if kind not in _KNOWN_STEPS:
        message = f"unknown fake agent script step {kind!r}"
        raise ValueError(message)
    return TurnStep(kind=kind, **fields)


class Script:
    """The ordered list of `TurnStep`s a REPL consumes one per turn, then defaults forever."""

    def __init__(self, steps: list[TurnStep]) -> None:
        """Hold `steps`, consumed from the start by `next_step()`."""
        self._steps = steps
        self._index = 0

    @classmethod
    def from_env(cls, raw: str | None) -> Script:
        """Parse `SHIKIRU_FAKE_SCRIPT`'s raw value, or build an empty (always-`accept`) script."""
        if not raw:
            return cls([])
        return cls([_step_from_json(item) for item in json.loads(raw)])

    def next_step(self) -> TurnStep:
        """Return the next scripted step, or `accept` once the script is exhausted."""
        if self._index >= len(self._steps):
            return TurnStep(kind=STEP_ACCEPT)
        step = self._steps[self._index]
        self._index += 1
        return step
