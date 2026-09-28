"""Small coloured percentage gauges (docs/design/06-operator.md §6.1)."""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text

from shikiru.ui.glyphs import GAUGE_GLYPHS
from shikiru.ui.tokens import gauge_color

GAUGE_LEVELS = len(GAUGE_GLYPHS) - 1
GAUGE_MAX_PCT = 100
PERCENT_FORMAT = ".0f"


@dataclass(frozen=True)
class Gauge:
    """Render a label and threshold-coloured five-state gauge."""

    label: str
    used_pct: float

    def render(self) -> Text:
        """Render the nearest gauge glyph and the unbounded reported percentage."""
        level = round(min(max(self.used_pct, 0.0), GAUGE_MAX_PCT) * GAUGE_LEVELS / GAUGE_MAX_PCT)
        glyph = GAUGE_GLYPHS[level]
        color = gauge_color(self.used_pct)
        return Text.assemble(
            (self.label, "dim"),
            (" "),
            (glyph, color),
            (f" {self.used_pct:{PERCENT_FORMAT}}%", color),
        )
