"""A `Static` that repaints only on changed output and reflows only when its size can change."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from rich.console import Group
from rich.rule import Rule
from rich.text import Text
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


@dataclass(frozen=True, slots=True)
class _Signature:
    width: int
    selection: object
    key: object


def _renderable_key(content: object) -> object | None:
    if isinstance(content, str):
        return content
    if type(content) is Group:
        keys = tuple(_renderable_key(item) for item in content.renderables)
        return None if None in keys else (content.fit, keys)
    if type(content) is Rule:
        return (content.title, content.characters, content.style, content.end, content.align)
    if type(content) is Text:
        return (
            content.plain,
            tuple(content.spans),
            content.style,
            content.justify,
            content.overflow,
            content.no_wrap,
            content.end,
            content.tab_size,
        )
    return None


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
        self._signature: _Signature | None = None

    def update(self, content: VisualType = "", *, layout: bool = True) -> None:
        """Update unconditionally and forget what was last shown."""
        self._shown = None
        self._signature = None
        super().update(content, layout=layout)

    def notify_style_update(self) -> None:
        """Forget the shown signature because the styles that shape the output changed."""
        self._signature = None
        super().notify_style_update()

    def update_if_changed(self, content: VisualType, *, key: object | None = None) -> bool:
        """Show `content` unless it renders identically to what is shown; True when updated.

        `key`, when given, asserts that `content` renders as a pure function of it and of the
        widget's width, so an equal key skips rendering altogether.
        """
        width = self.content_size.width
        has_content_sized_width = self.styles.width is not None and self.styles.width.is_auto
        if width <= 0 or has_content_sized_width:
            self.update(content)
            return True
        render_key = key if key is not None else _renderable_key(content)
        signature = (
            None
            if render_key is None
            else _Signature(width, self.text_selection, (self._render_markup, render_key))
        )
        if signature is not None and signature == self._signature:
            return False
        visual = visualize(self, content, markup=self._render_markup)
        shown = _Shown(
            width,
            tuple(Visual.to_strips(self, visual, width, None, self.visual_style)),
        )
        previous = self._shown
        if previous == shown:
            self._signature = signature
            return False
        super().update(content, layout=previous is None or not previous.has_same_shape(shown))
        self._shown = shown
        self._signature = signature
        return True
