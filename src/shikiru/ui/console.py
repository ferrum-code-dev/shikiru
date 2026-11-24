"""Human terminal output shared by CLI surfaces."""

from __future__ import annotations

import os

from rich.console import Console

OSC8_TERMINAL_PROGRAMS = frozenset(
    {
        "Hyper",
        "WezTerm",
        "contour",
        "ghostty",
        "vscode",
        "iTerm.app",
    }
)
OSC8_TERMINAL_NAMES = frozenset(
    {
        "alacritty",
        "contour",
        "foot",
        "kitty",
        "wezterm",
        "xterm-ghostty",
        "xterm-kitty",
    }
)
MIN_VTE_OSC8_VERSION = 5000
REMOTE_STDOUT_TERMINAL_ENV = "SHIKIRU_REMOTE_STDOUT_TERMINAL"


def human_console(*, stderr: bool = False) -> Console:
    """Return a Rich console that keeps human data literal and detects terminal output modes."""
    force_terminal = True if not stderr and os.environ.get(REMOTE_STDOUT_TERMINAL_ENV) else None
    return Console(
        stderr=stderr,
        force_terminal=force_terminal,
        soft_wrap=True,
        markup=False,
        highlight=False,
        emoji=False,
    )


def supports_osc8(console: Console) -> bool:
    """Return whether the active terminal is known to support OSC 8 hyperlinks."""
    if not console.is_terminal or console.is_dumb_terminal:
        return False
    if os.environ.get("WT_SESSION"):
        return True
    if os.environ.get("TERM_PROGRAM") in OSC8_TERMINAL_PROGRAMS:
        return True
    if os.environ.get("TERM") in OSC8_TERMINAL_NAMES:
        return True
    vte_version = os.environ.get("VTE_VERSION", "")
    return vte_version.isdecimal() and int(vte_version) >= MIN_VTE_OSC8_VERSION
