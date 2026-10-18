"""Keep a covered Actions list out of Textual's translucent background chain."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from textual.app import App

if TYPE_CHECKING:
    from textual.screen import Screen


class Coverable:
    """A screen that stands aside while a popup opened from it is on top."""

    covered: bool = False


class CoveredScreens(App[None]):
    """An app whose background chain skips covered screens.

    Textual renders each translucent screen over the one below it and hands the screens out
    through one shared list that each screen's own render pops once. A screen whose render is
    cached never pops, so with three translucent screens stacked the screen below it takes the
    wrong base and renders itself without end. A covered list draws nothing, so skipping it
    keeps the chain at two screens.
    """

    @property
    def _background_screens(self) -> list[Screen[object]]:
        stack = cast("list[Screen[object]]", super()._background_screens)  # pyright: ignore[reportUnknownMemberType]
        return [s for s in stack if not (isinstance(s, Coverable) and s.covered)]
