"""Escalation splash panels (docs/design/06-operator.md §6.4)."""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text

from shikiru.ui.tokens import ALERT_COLOR, STYLE_BOLD

_SPEED_LINE = "\N{BOX DRAWINGS LIGHT DIAGONAL UPPER RIGHT TO LOWER LEFT}"
_OPTION_PREFIX = "  "


@dataclass(frozen=True)
class SplashPanel:
    """Render an escalation headline, question and packet options in an alert frame."""

    headline: str
    question: str
    options: tuple[str, ...]

    def render(self) -> Text:
        """Render the panel with numbered options and red speed-line framing."""
        lines = Text()
        lines.append(f"{_SPEED_LINE} {_SPEED_LINE} {self.headline} {_SPEED_LINE} {_SPEED_LINE}\n")
        lines.append(self.question)
        for number, option in enumerate(self.options, start=1):
            lines.append(f"\n{_OPTION_PREFIX}{number}. {option}")
        lines.append(f"\n{_SPEED_LINE} {_SPEED_LINE} {_SPEED_LINE}")
        lines.stylize(f"{STYLE_BOLD} {ALERT_COLOR}")
        return lines
