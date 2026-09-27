"""Parses `SHIKIRU_FAKE_SCRIPT` into `TurnStep`s (see the `fake_agent` package docstring)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from shikiru.agents.contracts import FailureKind


class QuotaWindowStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    used_pct: float
    resets_at: str | None = None
    observed_at: str


class _StepModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quota_windows: list[QuotaWindowStep] = Field(default_factory=list)


class NewTaskSpec(_StepModel):
    """One `NewTask` a scripted `supersede` or `accept` (plan) step enqueues."""

    title: str
    request: str
    depends_on: list[str] = Field(default_factory=list)


class AcceptStep(_StepModel):
    """Submit a successful report for whatever `report.kind` the current turn dir names.

    `new_tasks` only takes effect for a `plan` report (03 §2's "fill `new_tasks` only when the
    request asks to execute the plan"); it is ignored for every other report kind.
    """

    kind: Literal["accept"] = "accept"
    new_tasks: list[NewTaskSpec] = Field(default_factory=list)
    uncommitted: str = ""
    corrupt_report: bool = False
    stall_after_report: bool = False


class SupersedeStep(_StepModel):
    """Submit a `supersede` verdict with `new_tasks`; only valid for `report.kind == verdict`."""

    kind: Literal["supersede"] = "supersede"
    new_tasks: list[NewTaskSpec]


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
    | SupersedeStep
)

_STEPS_ADAPTER: TypeAdapter[list[Annotated[TurnStep, Field(discriminator="kind")]]] = TypeAdapter(
    list[Annotated[TurnStep, Field(discriminator="kind")]]
)


class Script:
    """The ordered list of `TurnStep`s a REPL consumes one per turn, then defaults forever."""

    def __init__(self, steps: list[TurnStep], *, start_index: int = 0) -> None:
        """Hold `steps`, consumed from the start by `next_step()`."""
        self._steps = steps
        self._index = start_index

    @classmethod
    def from_env(cls, raw: str | None, *, start_index: int = 0) -> Script:
        """Parse `SHIKIRU_FAKE_SCRIPT`'s raw value, or build an empty (always-`accept`) script."""
        if not raw:
            return cls([], start_index=start_index)
        return cls(_STEPS_ADAPTER.validate_json(raw), start_index=start_index)

    def next_step(self) -> TurnStep:
        """Return the next scripted step, or `accept` once the script is exhausted."""
        if self._index >= len(self._steps):
            return AcceptStep()
        step = self._steps[self._index]
        self._index += 1
        return step

    @property
    def index(self) -> int:
        """Return the next script index to consume."""
        return self._index
