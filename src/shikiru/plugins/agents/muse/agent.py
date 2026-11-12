"""Muse Code headless turn plugin."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from shikiru.agents.contracts import (
    BILLING_SOURCE_OPERATOR_CONFIG,
    RUNTIME_DEFAULT_MODEL,
    UNKNOWN_BILLING_DECLARATION,
    BillingDeclaration,
)
from shikiru.agents.sdk.reset_time import ResetTimeParser
from shikiru.agents.sdk.turn import StreamingTurn
from shikiru.clock import from_local
from shikiru.plugins.agents.muse.options import (
    MODEL_FLAG,
    MUSE_COMMAND,
    MUSE_SETTINGS,
    REASONING_EFFORT_FLAG,
    RESUME_SUBCOMMAND,
    MuseOptions,
)
from shikiru.plugins.agents.muse.turn import MuseTurnSequence
from shikiru.plugins.agents.shared_settings import options_error

if TYPE_CHECKING:
    from collections.abc import Sequence

    from shikiru.agents.contracts import AgentContext, Rung, Turn, TurnSpec
    from shikiru.agents.settings import AgentSetting
    from shikiru.clock import Clock
    from shikiru.process import SubprocessRunner


class MuseAgent:
    """Build Muse Code turn processes and take-over commands."""

    name = "muse"

    def __init__(
        self,
        runner: SubprocessRunner,
        clock: Clock,
        options: MuseOptions,
        user_home: Path,
    ) -> None:
        self._runner = runner
        self._clock = clock
        self._options = options
        self._user_home = user_home
        self._reset_time = ResetTimeParser(clock, resolve_local=from_local)
        self._billing_declaration = (
            BillingDeclaration(options.billing_mode, BILLING_SOURCE_OPERATOR_CONFIG)
            if options.billing_mode is not None
            else UNKNOWN_BILLING_DECLARATION
        )

    @property
    def billing_declaration(self) -> BillingDeclaration:
        """Return the explicit plugin option or the unknown declaration."""
        return self._billing_declaration

    async def start_turn(self, spec: TurnSpec) -> Turn:
        """Start one Muse process-per-turn exchange."""
        sequence = MuseTurnSequence(
            args=self._options.args,
            base_environment=dict(os.environ),
            clock=self._clock,
            reset_time=self._reset_time,
            user_home=self._user_home,
            context_window_tokens=None,
            compact_at_pct=self._options.compact_at_pct,
        )
        return await StreamingTurn.start(spec, self._runner, sequence)

    def interactive_command(self, session_id: str, run_dir: Path, rung: Rung) -> Sequence[str]:
        """Return Muse's interactive resume command for a headless session and rung."""
        del run_dir
        argv = [MUSE_COMMAND, RESUME_SUBCOMMAND, session_id]
        if rung.model != RUNTIME_DEFAULT_MODEL:
            argv.extend((MODEL_FLAG, rung.model))
        if rung.effort is not None:
            argv.extend((REASONING_EFFORT_FLAG, rung.effort))
        argv.extend(self._options.args)
        return tuple(argv)

    def settings(self) -> tuple[AgentSetting, ...]:
        """Return the options worth writing to the project config."""
        return MUSE_SETTINGS


def create(context: AgentContext) -> MuseAgent:
    """Build the Muse plugin from validated `AgentContext.options`."""
    try:
        options = MuseOptions.model_validate(context.options)
    except ValidationError as error:
        raise options_error(MUSE_COMMAND, error) from error
    return MuseAgent(context.runner, context.clock, options, Path.home())
