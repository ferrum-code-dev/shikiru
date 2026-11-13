"""Small coloured percentage gauges."""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text

from shikiru.domain.levels import usage_level
from shikiru.ui.glyphs import GAUGE_GLYPHS
from shikiru.ui.tokens import SECONDARY_TEXT_COLOR, USAGE_LEVEL_COLORS

GAUGE_LEVELS = len(GAUGE_GLYPHS) - 1
GAUGE_MAX_PCT = 100
PERCENT_FORMAT = ".0f"
UNKNOWN_GAUGE_GLYPH = GAUGE_GLYPHS[0]
UNKNOWN_PCT_TEXT = "--%"


@dataclass(frozen=True)
class Gauge:
    """Render a label and a glyph and percentage coloured by the usage level they reach.

    A `used_pct` of None is an unknown share: an empty glyph and `--%` in the secondary colour.
    """

    label: str
    used_pct: float | None
    warn_pct: int
    alert_pct: int

    def render(self) -> Text:
        """Render the nearest glyph and the unbounded percentage, or the unknown form."""
        text = Text()
        if self.label:
            text.append(self.label, style="dim")
            text.append(" ")
        if self.used_pct is None:
            text.append(UNKNOWN_GAUGE_GLYPH, style=SECONDARY_TEXT_COLOR)
            text.append(f" {UNKNOWN_PCT_TEXT}", style=SECONDARY_TEXT_COLOR)
            return text
        level = round(min(max(self.used_pct, 0.0), GAUGE_MAX_PCT) * GAUGE_LEVELS / GAUGE_MAX_PCT)
        glyph = GAUGE_GLYPHS[level]
        color = USAGE_LEVEL_COLORS[
            usage_level(self.used_pct, warn_pct=self.warn_pct, alert_pct=self.alert_pct)
        ]
        text.append(glyph, style=color)
        text.append(f" {self.used_pct:{PERCENT_FORMAT}}%", style=color)
        return text
