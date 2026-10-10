"""A `Static` that repaints only on changed output and reflows only when its size can change."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from textual.visual import Visual, visualize
from textual.widgets import Static

if TYPE_CHECKING:
    from textual.strip import Strip
    from textual.visual import VisualType


@dataclass(frozen=True, slots=True)
class _Shown:
    width: int
    strips: tuple[Strip, ...]

    def has_same_shape(self, other: _Shown) -> bool:
        return self.width == other.width and self._extent() == other._extent()

    def _extent(self) -> tuple[int, int]:
        return len(self.strips), max((strip.cell_length for strip in self.strips), default=0)


class GatedStatic(Static):
    """Skips updates that show the same output; repaints without reflow when the extent holds."""

    def __init__(
        self,
        content: VisualType = "",
        *,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
        markup: bool = True,
    ) -> None:
        super().__init__(content, id=id, classes=classes, markup=markup)
        self._shown: _Shown | None = None

    def update(self, content: VisualType = "", *, layout: bool = True) -> None:
        """Update unconditionally and forget what was last shown."""
        self._shown = None
        super().update(content, layout=layout)

    def update_if_changed(self, content: VisualType) -> bool:
        """Show `content` unless it renders identically to what is shown; True when updated."""
        width = self.content_size.width
        has_content_sized_width = self.styles.width is not None and self.styles.width.is_auto
        if width <= 0 or has_content_sized_width:
            self.update(content)
            return True
        visual = visualize(self, content, markup=self._render_markup)
        shown = _Shown(
            width,
            tuple(Visual.to_strips(self, visual, width, None, self.visual_style)),
        )
        previous = self._shown
        if previous == shown:
            return False
        super().update(content, layout=previous is None or not previous.has_same_shape(shown))
        self._shown = shown
        return True
