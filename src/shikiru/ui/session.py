"""Shared facts for session listings and attach options."""

from __future__ import annotations

from typing import Literal

from rich.text import Text

from shikiru.ui.glyphs import GLYPH_ENGINE
from shikiru.ui.section import BOLD_PRIMARY, BOLD_STEEL, number
from shikiru.ui.tokens import AMBER, SECONDARY_TEXT_COLOR, STYLE_BOLD

_SessionEngineState = Literal["up", "down"]


def session_identity(session: str) -> Text:
    """Return a session identity as bold literal text."""
    return Text(session, style=BOLD_PRIMARY, overflow="fold")


def session_engine_value(engine_state: _SessionEngineState) -> Text:
    """Return the engine glyph and muted or active state word."""
    match engine_state:
        case "up":
            style = BOLD_STEEL
        case "down":
            style = SECONDARY_TEXT_COLOR
    return Text.assemble(
        (GLYPH_ENGINE, style),
        (" ", SECONDARY_TEXT_COLOR),
        (engine_state, style),
    )


def session_engine_fact(engine_state: _SessionEngineState) -> Text:
    """Return the labeled engine state used in a stacked session row."""
    text = Text("Engine ", style=SECONDARY_TEXT_COLOR)
    text.append_text(session_engine_value(engine_state))
    return text


def session_count(label: str, count: int) -> Text:
    """Return one labeled session count."""
    return Text.assemble((f"{label} ", SECONDARY_TEXT_COLOR), number(str(count)))


def escalation_count(count: int) -> Text:
    """Return an amber nonzero escalation count or a muted zero."""
    style = f"{STYLE_BOLD} {AMBER}" if count else SECONDARY_TEXT_COLOR
    return Text(str(count), style=style)


def session_facts(
    engine_state: _SessionEngineState,
    running: int,
    queued: int,
    escalated: int,
) -> tuple[Text, ...]:
    """Return engine and progress facts in their shared display order."""
    return (
        session_engine_fact(engine_state),
        session_count("running", running),
        session_count("queued", queued),
        Text.assemble(("escalated ", SECONDARY_TEXT_COLOR), escalation_count(escalated)),
    )
