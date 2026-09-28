"""Escalation splash panels (docs/design/06-operator.md §6.4)."""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text

from shikiru.ui.tokens import ALERT_COLOR, STYLE_BOLD, STYLE_DIM

_SPEED_LINE = "\N{BOX DRAWINGS LIGHT DIAGONAL UPPER RIGHT TO LOWER LEFT}"
_OPTION_PREFIX = "  "


@dataclass(frozen=True)
class SplashPanel:
    """Render an escalation headline, question and packet options in an alert frame."""

    headline: str
    question: str
    options: tuple[str, ...]
    pulse_red: bool = True

    def render(self) -> Text:
        """Render the panel with numbered options and red speed-line framing."""
        lines = Text()
        prefix = f"{_SPEED_LINE} {_SPEED_LINE} "
        suffix = f" {_SPEED_LINE} {_SPEED_LINE}"
        top = f"{prefix}{self.headline}{suffix}"
        lines.append(f"{top}\n")
        lines.append(self.question)
        for number, option in enumerate(self.options, start=1):
            lines.append(f"\n{_OPTION_PREFIX}{number}. {option}")
        bottom = f"{_SPEED_LINE} {_SPEED_LINE} {_SPEED_LINE}"
        lines.append(f"\n{bottom}")
        border_style = f"{STYLE_BOLD} {ALERT_COLOR}" if self.pulse_red else STYLE_DIM
        lines.stylize(border_style, 0, len(prefix))
        suffix_start = len(prefix) + len(self.headline)
        lines.stylize(border_style, suffix_start, len(top))
        bottom_start = len(lines.plain) - len(bottom)
        lines.stylize(border_style, bottom_start, len(lines.plain))
        return lines
