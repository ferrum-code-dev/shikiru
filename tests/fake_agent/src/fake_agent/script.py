"""Parses `SHIKIRU_FAKE_SCRIPT` into `TurnStep`s (see the `fake_agent` package docstring)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from shikiru.agents.contracts import FailureKind


class _StepModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AcceptStep(_StepModel):
    """Submit a successful report for whatever `report.kind` the current turn dir names."""

    kind: Literal["accept"] = "accept"


class ChangesRequestedStep(_StepModel):
    """Submit a `changes_requested` verdict; only valid when `report.kind` is `verdict`."""

    kind: Literal["changes_requested"] = "changes_requested"


class NeedsDecisionStep(_StepModel):
    """Submit a `needs_decision` report; only valid when `report.kind` is `implementation`."""

    kind: Literal["needs_decision"] = "needs_decision"
    question: str = ""


class NoReportStep(_StepModel):
    """End the turn without ever calling `shikiru report`."""

    kind: Literal["no_report"] = "no_report"


class FailStep(_StepModel):
    """Emit `TurnFailed` directly, without submitting a report."""

    kind: Literal["fail"] = "fail"
    failure_kind: FailureKind
    resets_at: str | None = None


class CrashStep(_StepModel):
    """Exit the REPL process non-zero, simulating a dead pane."""

    kind: Literal["crash"] = "crash"


class StallStep(_StepModel):
    """Read the prompt but never respond to it."""

    kind: Literal["stall"] = "stall"


class DialogStep(_StepModel):
    """Emit `AttentionNeeded` and leave the turn open."""

    kind: Literal["dialog"] = "dialog"
    message: str = ""


type TurnStep = (
    AcceptStep
    | ChangesRequestedStep
    | NeedsDecisionStep
    | NoReportStep
    | FailStep
    | CrashStep
    | StallStep
    | DialogStep
)

_STEPS_ADAPTER: TypeAdapter[list[Annotated[TurnStep, Field(discriminator="kind")]]] = TypeAdapter(
    list[Annotated[TurnStep, Field(discriminator="kind")]]
)


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
        return cls(_STEPS_ADAPTER.validate_json(raw))

    def next_step(self) -> TurnStep:
        """Return the next scripted step, or `accept` once the script is exhausted."""
        if self._index >= len(self._steps):
            return AcceptStep()
        step = self._steps[self._index]
        self._index += 1
        return step
