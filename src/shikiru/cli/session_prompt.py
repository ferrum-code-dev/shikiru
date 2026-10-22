"""Plain numbered session selection for the attach command."""

from __future__ import annotations

import signal
from typing import TYPE_CHECKING

from rich.text import Text

from shikiru.cli.render import session_options_renderable
from shikiru.errors import UsageError
from shikiru.ui.console import human_console
from shikiru.ui.section import BOLD_STEEL
from shikiru.ui.tokens import SECONDARY_TEXT_COLOR

if TYPE_CHECKING:
    from collections.abc import Sequence

    from shikiru.operator.sessions import SessionRow


SESSION_CANCELLED = "session selection was cancelled"
SESSION_PROMPT_PREFIX = "Select a session number (or "
SESSION_PROMPT_SUFFIX = " to cancel): "
SESSION_CANCEL_CHOICES = frozenset({"q", "quit"})


def prompt_for_session(rows: Sequence[SessionRow], host: str | None) -> SessionRow:
    """Show full session identities and return the validated numbered selection."""
    console = human_console()
    console.print(session_options_renderable(tuple(rows), host=host, console=console))
    while True:
        prompt = Text.assemble(
            (SESSION_PROMPT_PREFIX, SECONDARY_TEXT_COLOR),
            ("q", BOLD_STEEL),
            (SESSION_PROMPT_SUFFIX, SECONDARY_TEXT_COLOR),
        )
        console.print(prompt, end="")
        previous_handler = signal.signal(signal.SIGINT, _interrupt_input)
        try:
            answer = input().strip()
        except (EOFError, KeyboardInterrupt) as error:
            raise UsageError(SESSION_CANCELLED) from error
        finally:
            signal.signal(signal.SIGINT, previous_handler)
        if answer.lower() in SESSION_CANCEL_CHOICES:
            raise UsageError(SESSION_CANCELLED)
        if answer.isdecimal():
            try:
                selected = int(answer)
            except ValueError:
                pass
            else:
                if 1 <= selected <= len(rows):
                    return rows[selected - 1]
        retry = Text(f"Enter a number from 1 to {len(rows)}, or ", style=SECONDARY_TEXT_COLOR)
        retry.append("q", style=BOLD_STEEL)
        retry.append(" to cancel.", style=SECONDARY_TEXT_COLOR)
        console.print(retry)


def _interrupt_input(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt
